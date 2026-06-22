"""Data export utilities for dbsnap snapshots."""

import pyodbc
import struct
from datetime import datetime, date, time
from decimal import Decimal
from uuid import UUID


def _convert_datetimeoffset(raw):
    """Parse SQL Server datetimeoffset binary format into ISO 8601 string."""
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    try:
        if len(raw) < 20:
            return str(raw)
        year, month, day, hour, minute, second, fraction, tz_hour, tz_minute = \
            struct.unpack('<HHHHHHIhh', raw[:20])
        sign = '+' if tz_hour >= 0 else '-'
        return f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}.{fraction:07d}{sign}{abs(tz_hour):02d}:{abs(tz_minute):02d}"
    except Exception:
        return str(raw)
import struct


def _serialize_value(value):
    """Serialize a SQL value to a JSON-safe representation."""
    if value is None:
        return None
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return "0x" + value.hex()
    if isinstance(value, memoryview):
        return "0x" + value.tobytes().hex()
    return value


def _deserialize_value(value, col_type=None):
    """Deserialize a JSON value back to a SQL-compatible type.

    Converts ISO 8601 datetime strings back to Python datetime objects
    so pyodbc can properly bind them to SQL Server datetime columns.
    Converts hex strings (0x...) back to bytes.
    """
    if value is None:
        return None
    if isinstance(value, str) and value.startswith("0x"):
        try:
            return bytes.fromhex(value[2:])
        except (ValueError, TypeError):
            return value
    if isinstance(value, str) and ('T' in value or value.count('-') >= 2):
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            pass
    return value


def export_table_data(cursor, schema, table_name, batch_size=5000):
    """Export all rows from a table as a list of dicts.

    Args:
        cursor: pyodbc cursor
        schema: Table schema name
        table_name: Table name
        batch_size: Number of rows to fetch at a time

    Returns:
        List of dicts, one per row
    """
    cursor.execute(f"SELECT * FROM [{schema}].[{table_name}]")
    columns = [col[0] for col in cursor.description]
    
    rows = []
    while True:
        batch = cursor.fetchmany(batch_size)
        if not batch:
            break
        for row in batch:
            row_dict = {}
            for i, col in enumerate(columns):
                row_dict[col] = _serialize_value(row[i])
            rows.append(row_dict)
    
    return rows


def export_all_data(conn_str, tables, driver=None, trust_cert=False):
    """Export data from all tables in the snapshot.

    Args:
        conn_str: Connection string
        tables: Dict of table info from snapshot (with schema)
        driver: ODBC driver name
        trust_cert: Trust self-signed certificates

    Returns:
        Dict mapping table name to list of row dicts
    """
    from .extractor import connect_to_db
    
    conn = connect_to_db(conn_str, driver, trust_cert)
    # Handle SQL Server types that pyodbc doesn't support natively
    try:
        conn.add_output_converter(-155, _convert_datetimeoffset)  # datetimeoffset
    except Exception:
        pass
    try:
        conn.add_output_converter(-151, lambda val: str(val) if val else None)  # hierarchyid, geometry, geography
    except Exception:
        pass
    cursor = conn.cursor()
    
    data = {}
    total_rows = 0
    
    print("Exporting table data...")
    for full_key, table in tables.items():
        schema = table.get("schema", "dbo")
        parts = full_key.split(".", 1)
        name = parts[1] if len(parts) > 1 else full_key
        try:
            rows = export_table_data(cursor, schema, name)
            data[full_key] = rows
            total_rows += len(rows)
            print(f"  {name}: {len(rows)} rows")
        except Exception as e:
            print(f"  Warning: Failed to export {name}: {e}")
            data[full_key] = []
    
    conn.close()
    print(f"Total rows exported: {total_rows}")
    return data


def restore_table_data(cursor, schema, table_name, rows, identity_columns=None):
    """Insert rows into a table from snapshot data.

    Clears existing data first, then inserts in batches.
    Falls back to row-by-row insert if a batch fails.

    Args:
        cursor: pyodbc cursor
        schema: Table schema name
        table_name: Table name
        rows: List of dicts to insert
        identity_columns: List of column names that are identity columns

    Returns:
        Number of rows inserted
    """
    if not rows:
        return 0

    identity_columns = identity_columns or []
    # Case-insensitive check if first row has identity columns
    first_row_keys = {k.lower() for k in rows[0].keys()} if rows else set()
    has_identity = any(
        (isinstance(ic, str) and ic.lower() in first_row_keys)
        or ic in rows[0]
        for ic in identity_columns
    ) if rows else bool(identity_columns)

    # Clear existing data first to avoid PK conflicts
    try:
        cursor.execute(f"DELETE FROM [{schema}].[{table_name}];")
    except Exception:
        try:
            cursor.execute(f"TRUNCATE TABLE [{schema}].[{table_name}];")
        except Exception:
            pass

    columns = list(rows[0].keys())
    col_list = ", ".join(f"[{c}]" for c in columns)
    placeholders = ", ".join("?" for _ in columns)

    insert_sql = f"INSERT INTO [{schema}].[{table_name}] ({col_list}) VALUES ({placeholders})"

    if has_identity:
        try:
            cursor.execute(f"SET IDENTITY_INSERT [{schema}].[{table_name}] ON;")
        except Exception:
            pass

    inserted = 0
    batch = []

    def flush_batch():
        nonlocal inserted
        if not batch:
            return
        try:
            cursor.executemany(insert_sql, batch)
            inserted += len(batch)
            batch.clear()
        except Exception as e:
            failed = 0
            first_error = None
            for row_values in batch:
                try:
                    cursor.execute(insert_sql, row_values)
                    inserted += 1
                except Exception as row_err:
                    if first_error is None:
                        first_error = str(row_err)
                    failed += 1
            if failed:
                print(f"    Warning: {failed} rows skipped in {table_name} — {first_error}")
            batch.clear()

    for row in rows:
        values = [_deserialize_value(row.get(c)) for c in columns]
        batch.append(values)

        if len(batch) >= 1000:
            flush_batch()

    flush_batch()

    if has_identity:
        try:
            cursor.execute(f"SET IDENTITY_INSERT [{schema}].[{table_name}] OFF;")
        except Exception:
            pass

    return inserted
