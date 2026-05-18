"""CLI entry point for dbsnap."""

import os
import sys
import click

from . import __version__
from .extractor import extract_full_schema, connect_to_db
from .snapshot import create_snapshot, save_snapshot, load_snapshot, get_snapshot_info
from .comparator import compare_all_categories, filter_items, get_summary
from .reporter import generate_report
from .restorer import restore_snapshot


@click.group()
@click.version_option(version=__version__, prog_name="dbsnap")
def main():
    """dbsnap - SQL Server Schema Snapshot & Diff Tool"""
    pass


@main.command()
@click.option("--conn", required=True, help="SQL Server connection string")
@click.option("--out", required=True, type=click.Path(), help="Output .dbsnap file path")
@click.option("--driver", default=None, help="ODBC driver name (auto-detected if not specified)")
@click.option("--trust-server-cert", is_flag=True, help="Trust self-signed certificates")
@click.option("--no-progress", is_flag=True, help="Disable progress output")
def snapshot(conn, out, driver, trust_server_cert, no_progress):
    """Take a snapshot of a SQL Server database schema."""
    try:
        extracted = extract_full_schema(
            conn_str=conn,
            driver=driver,
            trust_cert=trust_server_cert,
            show_progress=not no_progress,
        )
        
        snap = create_snapshot(
            extracted,
            server=extracted.get("_meta", {}).get("server"),
            database=extracted.get("_meta", {}).get("database"),
        )
        
        save_snapshot(snap, out)
        
        file_size = os.path.getsize(out)
        click.echo(f"\nSnapshot saved to: {out}")
        click.echo(f"File size: {_format_size(file_size)}")
        click.echo(f"Tables: {len(snap['tables'])}")
        click.echo(f"Procedures: {len(snap['procedures'])}")
        click.echo(f"Functions: {len(snap['functions'])}")
        click.echo(f"Triggers: {len(snap['triggers'])}")
        
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@main.command(name="compare")
@click.argument("left", type=click.Path(exists=True))
@click.argument("right", required=False, type=click.Path(exists=True))
@click.option("--conn", default=None, help="Connection string for live database comparison (instead of right snapshot)")
@click.option("--out", required=True, type=click.Path(), help="Output HTML report file path")
@click.option("--driver", default=None, help="ODBC driver name (for live comparison)")
@click.option("--trust-server-cert", is_flag=True, help="Trust self-signed certificates")
@click.option("--no-identical", is_flag=True, help="Exclude identical items from report")
@click.option("--schema-only", is_flag=True, help="Only compare tables")
@click.option("--procs-only", is_flag=True, help="Only compare procedures")
@click.option("--functions-only", is_flag=True, help="Only compare functions")
@click.option("--triggers-only", is_flag=True, help="Only compare triggers")
def compare(left, right, conn, out, driver, trust_server_cert, no_identical,
            schema_only, procs_only, functions_only, triggers_only):
    """Compare two snapshots or a snapshot vs a live database."""
    try:
        left_snap = load_snapshot(left)
        left_name = os.path.basename(left)
        left_meta = left_snap.get("meta", {})
        
        if conn:
            click.echo(f"Extracting live schema from: {conn[:30]}...")
            extracted = extract_full_schema(
                conn_str=conn,
                driver=driver,
                trust_cert=trust_server_cert,
                show_progress=True,
            )
            right_snap = create_snapshot(
                extracted,
                server=extracted.get("_meta", {}).get("server"),
                database=extracted.get("_meta", {}).get("database"),
            )
            right_name = f"Live: {right_snap['meta']['database']}"
            right_meta = right_snap.get("meta", {})
        elif right:
            right_snap = load_snapshot(right)
            right_name = os.path.basename(right)
            right_meta = right_snap.get("meta", {})
        else:
            click.echo("Error: Provide either a second snapshot file or --conn for live comparison", err=True)
            sys.exit(1)
        
        categories = _get_categories(schema_only, procs_only, functions_only, triggers_only)
        
        click.echo("Comparing snapshots...")
        comparison = compare_all_categories(left_snap, right_snap, categories)
        
        if no_identical:
            for cat in comparison:
                comparison[cat] = filter_items(comparison[cat], exclude_identical=True)
        
        summary = get_summary(comparison)
        _print_summary(summary)
        
        click.echo(f"\nGenerating HTML report...")
        generate_report(
            comparison=comparison,
            output_path=out,
            left_name=left_name,
            right_name=right_name,
            left_meta=left_meta,
            right_meta=right_meta,
        )
        
        click.echo(f"Report saved to: {out}")
        
        total_changes = sum(
            s.get("modified", 0) + s.get("only_in_left", 0) + s.get("only_in_right", 0)
            for s in summary.values()
        )
        sys.exit(0 if total_changes == 0 else 2)
        
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@main.command()
@click.argument("snapshot", type=click.Path(exists=True))
def info(snapshot):
    """Display information about a snapshot file."""
    try:
        snap_info = get_snapshot_info(snapshot)
        
        click.echo(f"Snapshot: {os.path.basename(snapshot)}")
        click.echo(f"{'─' * 40}")
        click.echo(f"Tool version: {snap_info['tool_version']}")
        click.echo(f"Created at:   {snap_info['created_at']}")
        click.echo(f"Server:       {snap_info['server']}")
        click.echo(f"Database:     {snap_info['database']}")
        click.echo(f"{'─' * 40}")
        click.echo(f"Tables:       {snap_info['table_count']}")
        click.echo(f"Procedures:   {snap_info['procedure_count']}")
        click.echo(f"Functions:    {snap_info['function_count']}")
        click.echo(f"Triggers:     {snap_info['trigger_count']}")
        click.echo(f"{'─' * 40}")
        click.echo(f"File size:    {_format_size(snap_info['file_size'])}")
        
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@main.command()
@click.argument("snapshot", type=click.Path(exists=True))
@click.option("--conn", required=True, help="Target database connection string")
@click.option("--driver", default=None, help="ODBC driver name (auto-detected if not specified)")
@click.option("--trust-server-cert", is_flag=True, help="Trust self-signed certificates")
@click.option("--dry-run", is_flag=True, help="Print SQL without executing")
@click.option("--schema-only", is_flag=True, default=True, help="Only restore schema (default)")
@click.option("--with-data", is_flag=True, help="Also restore table data (if present in snapshot)")
def restore(snapshot, conn, driver, trust_server_cert, dry_run, schema_only, with_data):
    """Restore a .dbsnap file to a target database."""
    try:
        snap_info = get_snapshot_info(snapshot)
        click.echo(f"Restoring: {os.path.basename(snapshot)}")
        click.echo(f"  From: {snap_info['server']}/{snap_info['database']}")
        click.echo(f"  Tables: {snap_info['table_count']}, Procedures: {snap_info['procedure_count']}")
        click.echo(f"  Functions: {snap_info['function_count']}, Triggers: {snap_info['trigger_count']}")
        click.echo()

        if dry_run:
            click.echo("-- DRY RUN --")

        stats = restore_snapshot(
            filepath=snapshot,
            conn_str=conn,
            driver=driver,
            trust_cert=trust_server_cert,
            schema_only=schema_only and not with_data,
            dry_run=dry_run,
        )

        click.echo()
        click.echo(f"Restore complete:")
        click.echo(f"  Tables: {stats['tables']}")
        click.echo(f"  Indexes: {stats['indexes']}")
        click.echo(f"  Foreign Keys: {stats['foreign_keys']}")
        click.echo(f"  Procedures: {stats['procedures']}")
        click.echo(f"  Functions: {stats['functions']}")
        click.echo(f"  Triggers: {stats['triggers']}")

    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def _get_categories(schema_only, procs_only, functions_only, triggers_only):
    """Determine which categories to compare based on flags."""
    if schema_only:
        return ["tables"]
    if procs_only:
        return ["procedures"]
    if functions_only:
        return ["functions"]
    if triggers_only:
        return ["triggers"]
    return None


def _print_summary(summary):
    """Print comparison summary to terminal."""
    for category, counts in summary.items():
        total = counts["modified"] + counts["only_in_left"] + counts["only_in_right"] + counts["identical"]
        if total == 0:
            continue
        
        parts = []
        if counts["modified"]:
            parts.append(f"{counts['modified']} modified")
        if counts["only_in_left"]:
            parts.append(f"{counts['only_in_left']} only in left")
        if counts["only_in_right"]:
            parts.append(f"{counts['only_in_right']} only in right")
        if counts["identical"]:
            parts.append(f"{counts['identical']} identical")
        
        click.echo(f"{category.capitalize()}: {', '.join(parts)}")


def _format_size(size_bytes):
    """Format file size in human-readable format."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


if __name__ == "__main__":
    main()
