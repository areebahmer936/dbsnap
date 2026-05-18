"""Database restoration from .dbsnap files."""

import pyodbc
from .extractor import connect_to_db, detect_driver
from .snapshot import load_snapshot


def _sql_type(col):
    """Build a SQL type string from a column dict."""
    t = col["type"]
    return t


def _build_create_table(name, table):
    """Generate a CREATE TABLE statement from snapshot data."""
    schema = table.get("schema", "dbo")
    lines = []
    lines.append(f"CREATE TABLE [{schema}].[{name}] (")

    col_defs = []
    pk_cols = []
    for col in table.get("columns", []):
        parts = [f"    [{col['name']}] {_sql_type(col)}"]
        if col.get("identity"):
            parts.append("IDENTITY(1,1)")
        if not col.get("nullable"):
            parts.append("NOT NULL")
        if col.get("default"):
            parts.append(f"DEFAULT {col['default']}")
        col_defs.append(" ".join(parts))

    # Find primary key columns
    for idx in table.get("indexes", []):
        if idx.get("unique") and "CLUSTERED" in idx.get("type", "") and "PK" in idx.get("name", ""):
            keys = [k.split()[0].strip("[]") for k in idx.get("keys", [])]
            pk_cols = keys
            break

    if pk_cols:
        pk_str = ", ".join(f"[{c}]" for c in pk_cols)
        col_defs.append(f"    CONSTRAINT [PK_{name}] PRIMARY KEY CLUSTERED ({pk_str})")

    lines.append(",\n".join(col_defs))
    lines.append(");")
    return "\n".join(lines)


def _build_create_index(table_name, table):
    """Generate CREATE INDEX statements."""
    schema = table.get("schema", "dbo")
    statements = []
    for idx in table.get("indexes", []):
        if "PK" in idx.get("name", ""):
            continue
        unique = "UNIQUE " if idx.get("unique") else ""
        keys = ", ".join(idx.get("keys", []))
        stmt = f"CREATE {unique}{idx['type']} INDEX [{idx['name']}] ON [{schema}].[{table_name}] ({keys});"
        if idx.get("included"):
            included = ", ".join(f"[{c}]" for c in idx["included"])
            stmt = stmt.rstrip(";") + f" INCLUDE ({included});"
        statements.append(stmt)
    return statements


def _build_create_fk(table_name, table):
    """Generate ALTER TABLE ADD CONSTRAINT statements for foreign keys."""
    schema = table.get("schema", "dbo")
    statements = []
    for fk in table.get("foreign_keys", []):
        on_delete = fk.get("on_delete", "NO_ACTION")
        on_update = fk.get("on_update", "NO_ACTION")
        stmt = (
            f"ALTER TABLE [{schema}].[{table_name}] "
            f"ADD CONSTRAINT [{fk['name']}] FOREIGN KEY ([{fk['from']}]) "
            f"REFERENCES [{fk['to_table']}].[{fk['to_column']}] "
            f"ON DELETE {on_delete} ON UPDATE {on_update};"
        )
        statements.append(stmt)
    return statements


def _topological_sort_tables(tables):
    """Sort tables by FK dependencies so referenced tables are created first."""
    # Build dependency graph
    deps = {}
    for name, table in tables.items():
        ref_tables = set()
        for fk in table.get("foreign_keys", []):
            to_table = fk.get("to_table", "")
            # Extract table name from schema.table format
            if "." in to_table:
                to_table = to_table.split(".")[-1]
            if to_table != name and to_table in tables:
                ref_tables.add(to_table)
        deps[name] = ref_tables

    # Topological sort using Kahn's algorithm
    in_degree = {name: 0 for name in tables}
    for name, refs in deps.items():
        for ref in refs:
            if ref in in_degree:
                in_degree[name] = in_degree.get(name, 0)  # ensure exists

    # Recalculate in-degree properly
    in_degree = {name: 0 for name in tables}
    for name, refs in deps.items():
        in_degree[name] = len(refs)

    queue = [name for name, deg in in_degree.items() if deg == 0]
    result = []
    visited = set()

    while queue:
        queue.sort()  # deterministic order
        name = queue.pop(0)
        if name in visited:
            continue
        visited.add(name)
        result.append(name)

        for other_name, other_refs in deps.items():
            if name in other_refs:
                other_refs.discard(name)
                if len(other_refs) == 0 and other_name not in visited:
                    queue.append(other_name)

    # Add any remaining tables (circular deps or isolated)
    for name in tables:
        if name not in visited:
            result.append(name)

    return result


def _drop_existing_fks(cursor, tables):
    """Drop all existing FK constraints in the target database for the tables we're restoring."""
    dropped = 0
    # Query the database for ALL FK constraints on our tables
    table_names = list(tables.keys())
    placeholders = ",".join(["?"] * len(table_names))
    cursor.execute(f"""
        SELECT 
            SCHEMA_NAME(fk.schema_id) AS schema_name,
            OBJECT_NAME(fk.parent_object_id) AS table_name,
            fk.name AS fk_name
        FROM sys.foreign_keys fk
        WHERE OBJECT_NAME(fk.parent_object_id) IN ({placeholders})
    """, *table_names)
    
    for schema_name, table_name, fk_name in cursor.fetchall():
        try:
            cursor.execute(f"ALTER TABLE [{schema_name}].[{table_name}] DROP CONSTRAINT [{fk_name}];")
            dropped += 1
        except Exception:
            pass
    return dropped


def _drop_existing_tables(cursor, tables):
    """Drop all existing tables, handling FK dependencies."""
    dropped = 0
    # Keep trying to drop tables until none can be dropped
    remaining = list(tables.keys())
    max_iterations = len(remaining) + 5
    iteration = 0
    
    while remaining and iteration < max_iterations:
        iteration += 1
        still_remaining = []
        for name in remaining:
            schema = tables[name].get("schema", "dbo")
            try:
                cursor.execute(f"DROP TABLE [{schema}].[{name}];")
                dropped += 1
            except Exception:
                still_remaining.append(name)
        remaining = still_remaining
    
    # Report any tables that couldn't be dropped
    for name in remaining:
        schema = tables[name].get("schema", "dbo")
        print(f"  Warning: Could not drop existing table [{schema}].[{name}]")
    
    return dropped


def restore_snapshot(filepath, conn_str, driver=None, trust_cert=False, schema_only=True, dry_run=False):
    """Restore a .dbsnap file to a target database.

    Args:
        filepath: Path to .dbsnap file
        conn_str: Target database connection string
        driver: ODBC driver name
        trust_cert: Trust self-signed certificates
        schema_only: Only restore schema, not data
        dry_run: Print SQL without executing

    Returns:
        Dict with counts of restored objects
    """
    snapshot = load_snapshot(filepath)
    conn = connect_to_db(conn_str, driver, trust_cert)
    cursor = conn.cursor()

    stats = {"tables": 0, "indexes": 0, "foreign_keys": 0, "procedures": 0, "functions": 0, "triggers": 0}

    try:
        cursor.execute("SET NOCOUNT ON;")

        tables = snapshot.get("tables", {})
        ordered = _topological_sort_tables(tables)

        if not dry_run:
            # Pre-step: Drop existing FKs first, then tables
            print("  Dropping existing foreign keys...")
            _drop_existing_fks(cursor, tables)
            conn.commit()

            print("  Dropping existing tables...")
            _drop_existing_tables(cursor, tables)
            conn.commit()

        # 1. Create tables in dependency order (without FKs)
        for name in ordered:
            table = tables[name]
            # Temporarily remove FKs for table creation
            fks = table.get("foreign_keys", [])
            table["foreign_keys"] = []
            sql = _build_create_table(name, table)
            table["foreign_keys"] = fks

            if dry_run:
                print(f"-- {sql}")
            else:
                try:
                    cursor.execute(sql)
                    conn.commit()
                    stats["tables"] += 1
                    print(f"  Created table: {name}")
                except pyodbc.ProgrammingError as e:
                    if "already an object named" in str(e):
                        print(f"  Skipped table (exists): {name}")
                    else:
                        print(f"  Warning: Failed to create table {name}: {e}")
                except Exception as e:
                    print(f"  Warning: Failed to create table {name}: {e}")

        # 2. Create indexes
        for name in ordered:
            table = tables[name]
            for stmt in _build_create_index(name, table):
                if dry_run:
                    print(f"-- {stmt}")
                else:
                    try:
                        cursor.execute(stmt)
                        conn.commit()
                        stats["indexes"] += 1
                    except Exception as e:
                        print(f"  Warning: Failed to create index on {name}: {e}")

        # 3. Create foreign keys
        for name in ordered:
            table = tables[name]
            for stmt in _build_create_fk(name, table):
                if dry_run:
                    print(f"-- {stmt}")
                else:
                    try:
                        cursor.execute(stmt)
                        conn.commit()
                        stats["foreign_keys"] += 1
                    except Exception as e:
                        print(f"  Warning: Failed to create FK on {name}: {e}")

        # 4. Create procedures
        for name, proc in snapshot.get("procedures", {}).items():
            definition = proc.get("definition", "")
            if dry_run:
                print(f"-- {definition[:80]}...")
            else:
                try:
                    cursor.execute(definition)
                    conn.commit()
                    stats["procedures"] += 1
                    print(f"  Created procedure: {name}")
                except Exception as e:
                    print(f"  Warning: Failed to create procedure {name}: {e}")

        # 5. Create functions
        for name, func in snapshot.get("functions", {}).items():
            definition = func.get("definition", "")
            if dry_run:
                print(f"-- {definition[:80]}...")
            else:
                try:
                    cursor.execute(definition)
                    conn.commit()
                    stats["functions"] += 1
                    print(f"  Created function: {name}")
                except Exception as e:
                    print(f"  Warning: Failed to create function {name}: {e}")

        # 6. Create triggers
        for name, trig in snapshot.get("triggers", {}).items():
            definition = trig.get("definition", "")
            if dry_run:
                print(f"-- {definition[:80]}...")
            else:
                try:
                    cursor.execute(definition)
                    conn.commit()
                    stats["triggers"] += 1
                    print(f"  Created trigger: {name}")
                except Exception as e:
                    print(f"  Warning: Failed to create trigger {name}: {e}")

    finally:
        conn.close()

    return stats
