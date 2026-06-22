"""Database restoration from .dbsnap files."""

import pyodbc
from tqdm import tqdm
from .extractor import connect_to_db, detect_driver
from .snapshot import load_snapshot_schema, stream_data_tables
from .data_exporter import restore_table_data, _deserialize_value


def _create_schemas(cursor, tables):
    """Create any non-dbo schemas referenced by tables in the snapshot."""
    schemas = set()
    for table in tables.values():
        schema = table.get("schema", "dbo")
        if schema.lower() != "dbo":
            schemas.add(schema)
    for schema in sorted(schemas):
        try:
            cursor.execute(f"IF SCHEMA_ID('{schema}') IS NULL EXEC('CREATE SCHEMA [{schema}]');")
        except Exception:
            pass


def _sql_type(col):
    """Build a SQL type string from a column dict."""
    t = col["type"]
    return t


def _table_schema_and_name(key, table):
    """Get schema and simple table name from snapshot key and table data."""
    schema = table.get("schema", "dbo")
    parts = key.split(".", 1)
    name = parts[1] if len(parts) > 1 else key
    return schema, name


def _build_create_table(full_key, table):
    """Generate a CREATE TABLE statement from snapshot data."""
    schema, name = _table_schema_and_name(full_key, table)
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


def _build_create_index(full_key, table):
    """Generate CREATE INDEX statements."""
    schema, name = _table_schema_and_name(full_key, table)
    statements = []
    for idx in table.get("indexes", []):
        if "PK" in idx.get("name", ""):
            continue
        unique = "UNIQUE " if idx.get("unique") else ""
        keys = ", ".join(idx.get("keys", []))
        stmt = f"CREATE {unique}{idx['type']} INDEX [{idx['name']}] ON [{schema}].[{name}] ({keys});"
        if idx.get("included"):
            included = ", ".join(f"[{c}]" for c in idx["included"])
            stmt = stmt.rstrip(";") + f" INCLUDE ({included});"
        statements.append(stmt)
    return statements


def _build_create_fk(full_key, table):
    """Generate ALTER TABLE ADD CONSTRAINT statements for foreign keys."""
    schema, name = _table_schema_and_name(full_key, table)
    statements = []
    for fk in table.get("foreign_keys", []):
        on_delete = fk.get("on_delete", "NO_ACTION")
        on_update = fk.get("on_update", "NO_ACTION")
        stmt = (
            f"ALTER TABLE [{schema}].[{name}] "
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
    # Build a lookup: simple_name -> full_key (for FK references that use simple names)
    name_lookup = {}
    for full_key in tables:
        _, simple = _table_schema_and_name(full_key, tables[full_key])
        name_lookup[simple] = full_key

    for full_key, table in tables.items():
        ref_tables = set()
        for fk in table.get("foreign_keys", []):
            to_table = fk.get("to_table", "")
            if to_table == full_key:
                continue
            # Try compound match first, then simple name lookup
            if to_table in tables:
                ref_tables.add(to_table)
            elif to_table in name_lookup:
                ref_tables.add(name_lookup[to_table])
        deps[full_key] = ref_tables

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
    """Drop all existing FK constraints on tables we're restoring."""
    dropped = 0
    for full_key in tables:
        schema, name = _table_schema_and_name(full_key, tables[full_key])
        try:
            cursor.execute(f"""
                DECLARE @sql NVARCHAR(MAX) = '';
                SELECT @sql = @sql + 'ALTER TABLE [{schema}].[{name}] DROP CONSTRAINT [' + fk.name + ']; '
                FROM sys.foreign_keys fk
                WHERE fk.parent_object_id = OBJECT_ID('[{schema}].[{name}]');
                EXEC sp_executesql @sql;
            """)
            dropped += 1
        except Exception:
            pass
    return dropped


def _drop_existing_tables(cursor, tables):
    """Drop all existing tables, handling FK dependencies."""
    dropped = 0
    remaining = list(tables.keys())
    max_iterations = len(remaining) + 5
    iteration = 0
    
    while remaining and iteration < max_iterations:
        iteration += 1
        still_remaining = []
        for full_key in remaining:
            schema, name = _table_schema_and_name(full_key, tables[full_key])
            try:
                cursor.execute(f"DROP TABLE [{schema}].[{name}];")
                dropped += 1
            except Exception:
                still_remaining.append(full_key)
        remaining = still_remaining
    
    for full_key in remaining:
        schema, name = _table_schema_and_name(full_key, tables[full_key])
        print(f"  Warning: Could not drop existing table [{schema}].[{name}]")
    
    return dropped


def restore_snapshot(filepath, conn_str, driver=None, trust_cert=False, schema_only=True, with_data=False, dry_run=False):
    """Restore a .dbsnap file to a target database.

    Args:
        filepath: Path to .dbsnap file
        conn_str: Target database connection string
        driver: ODBC driver name
        trust_cert: Trust self-signed certificates
        schema_only: Only restore schema, not data
        with_data: Also restore table data if present in snapshot
        dry_run: Print SQL without executing

    Returns:
        Dict with counts of restored objects
    """
    snapshot = load_snapshot_schema(filepath)
    conn = connect_to_db(conn_str, driver, trust_cert)
    cursor = conn.cursor()

    stats = {"tables": 0, "indexes": 0, "foreign_keys": 0, "procedures": 0, "functions": 0, "triggers": 0, "rows": 0}
    failed_tables = []
    failed_procs = []
    failed_funcs = []
    failed_trigs = []
    expected_fks = 0
    failed_fks = 0
    expected_idxs = 0
    failed_idxs = 0

    try:
        cursor.execute("SET NOCOUNT ON;")

        tables = snapshot.get("tables", {})
        ordered = _topological_sort_tables(tables)

        if not dry_run:
            # Pre-step: Create non-dbo schemas
            _create_schemas(cursor, tables)
            conn.commit()
            
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
                except pyodbc.ProgrammingError as e:
                    if "already an object named" in str(e):
                        stats["tables"] += 1  # Count existing tables too
                    else:
                        failed_tables.append((name, str(e)))
                except Exception as e:
                    failed_tables.append((name, str(e)))

        if failed_tables:
            print(f"  {len(failed_tables)} tables failed to create:")
            for tname, terr in failed_tables:
                print(f"    - {tname}: {terr}")

        # 2. Create indexes
        expected_idxs = sum(len(t.get("indexes", [])) for t in tables.values())
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
                        failed_idxs += 1

        # 3. Create foreign keys
        expected_fks = sum(len(t.get("foreign_keys", [])) for t in tables.values())
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
                        failed_fks += 1

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

        # 7. Restore data (if present and requested)
        if with_data:
            print("\n  Restoring data...")

            if not dry_run:
                print("  Disabling all foreign key constraints...")
                cursor.execute("EXEC sp_msforeachtable 'ALTER TABLE ? NOCHECK CONSTRAINT ALL';")
                conn.commit()

            data_tables = list(stream_data_tables(filepath))
            pbar = tqdm(data_tables, desc="  Tables", unit="table") if not dry_run else data_tables

            for table_name, rows in pbar:
                if table_name not in tables:
                    continue
                table = tables[table_name]
                schema, name = _table_schema_and_name(table_name, table)
                if not rows:
                    continue
                if dry_run:
                    print(f"-- Would insert {len(rows)} rows into [{schema}].[{name}]")
                else:
                    try:
                        identity_cols = [c["name"] for c in table.get("columns", []) if c.get("identity")]
                        if not identity_cols and rows:
                            first_row = rows[0]
                            for col_name in first_row.keys():
                                for c in table.get("columns", []):
                                    if c["name"].lower() == col_name.lower() and c.get("identity"):
                                        identity_cols.append(c["name"])
                                        break
                        inserted = restore_table_data(cursor, schema, name, rows, identity_cols)
                        stats["rows"] += inserted
                        if inserted > 0:
                            pbar.write(f"  Inserted {inserted} rows into {name}")
                        conn.commit()
                    except Exception as e:
                        pbar.write(f"  Warning: Failed to restore data for {name}: {e}")

            if not dry_run:
                print("  Re-enabling foreign key constraints with check...")
                cursor.execute("EXEC sp_msforeachtable 'ALTER TABLE ? WITH CHECK CHECK CONSTRAINT ALL';")
                conn.commit()

    finally:
        conn.close()

    # Print summary of failures
    if failed_tables:
        print(f"\n  Tables failed: {len(failed_tables)}")
        for name, err in failed_tables[:5]:
            print(f"    - {name}: {err[:100]}")
    if failed_fks:
        print(f"  Foreign keys failed: {failed_fks} (expected ~{expected_fks})")
    if failed_idxs:
        print(f"  Indexes failed: {failed_idxs} (expected ~{expected_idxs})")

    return stats
