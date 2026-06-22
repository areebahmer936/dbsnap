"""Database extraction utilities for SQL Server."""

import pyodbc
from tqdm import tqdm
from .hasher import compute_hash, compute_table_hash


KNOWN_DRIVERS = [
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "ODBC Driver 13 for SQL Server",
    "FreeTDS",
]


def detect_driver(preferred: str = None) -> str:
    """Detect an available ODBC driver for SQL Server.
    
    Args:
        preferred: Preferred driver name to try first
        
    Returns:
        Name of an available driver
        
    Raises:
        RuntimeError: If no suitable driver is found
    """
    available = pyodbc.drivers()
    
    if preferred and preferred in available:
        return preferred
    
    for driver in KNOWN_DRIVERS:
        if driver in available:
            return driver
    
    raise RuntimeError(
        f"No suitable ODBC driver found. Available drivers: {available}\n"
        f"Install 'ODBC Driver 18 for SQL Server' or 'ODBC Driver 17 for SQL Server'."
    )


import re

import pyodbc
from tqdm import tqdm
from .hasher import compute_hash, compute_table_hash


ODBC_BOOL_ATTRS = {"encrypt", "trustservercertificate", "trust server certificate",
                     "mars", "multipleactiveresultsets", "multiple active result sets",
                     "integratedsecurity", "integrated security"}

CONN_STR_ALIASES = {
    "user id": "UID",
    "uid": "UID",
    "password": "PWD",
    "pwd": "PWD",
    "server": "SERVER",
    "data source": "SERVER",
    "database": "DATABASE",
    "initial catalog": "DATABASE",
    "trust server certificate": "TrustServerCertificate",
    "multiple active result sets": "MultipleActiveResultSets",
    "trusted connection": "Integrated Security",
}

SPECIAL_CHARS = set("#;{}")


def normalize_conn_str_for_odbc(conn_str: str) -> str:
    """Normalize connection string attributes for ODBC compatibility.
    
    - Converts .NET SqlClient aliases to ODBC canonical names
    - Converts true/false values to yes/no
    - Wraps values containing special characters (#, ;, {}) in braces
    """
    def replace_key_value(match):
        key = match.group(1)
        value = match.group(2)
        
        normalized_key = CONN_STR_ALIASES.get(key.lower(), key)
        
        if value.startswith("{") and value.endswith("}"):
            return f"{normalized_key}={value}"
        
        if any(c in SPECIAL_CHARS for c in value):
            value = "{" + value + "}"
        
        return f"{normalized_key}={value}"
    
    conn_str = re.sub(r'([^=;]+)=([^;]*)', replace_key_value, conn_str)
    
    def replace_bool(match):
        key = match.group(1)
        value = match.group(2).lower()
        if value == "true":
            return f"{key}=yes"
        elif value == "false":
            return f"{key}=no"
        return match.group(0)
    
    pattern = r'(' + '|'.join(ODBC_BOOL_ATTRS) + r')=(true|false)'
    return re.sub(pattern, replace_bool, conn_str, flags=re.IGNORECASE)


def build_connection_string(conn_str: str, driver: str = None, trust_cert: bool = False) -> str:
    """Build a complete connection string with driver and trust settings.
    
    Args:
        conn_str: Base connection string
        driver: ODBC driver name (auto-detected if None)
        trust_cert: Whether to add TrustServerCertificate=yes
        
    Returns:
        Complete connection string
    """
    conn_str = normalize_conn_str_for_odbc(conn_str)
    
    parts = []
    
    if driver is None:
        driver = detect_driver()
    
    parts.append(f"DRIVER={{{driver}}}")
    
    if trust_cert:
        parts.append("TrustServerCertificate=yes")
    
    parts.append(conn_str)
    
    return ";".join(parts)


def connect_to_db(conn_str: str, driver: str = None, trust_cert: bool = False) -> pyodbc.Connection:
    """Connect to a SQL Server database.
    
    Args:
        conn_str: Connection string (without DRIVER prefix)
        driver: ODBC driver name (auto-detected if None)
        trust_cert: Whether to trust self-signed certificates
        
    Returns:
        pyodbc connection object
        
    Raises:
        pyodbc.Error: If connection fails
    """
    full_conn_str = build_connection_string(conn_str, driver, trust_cert)
    return pyodbc.connect(full_conn_str)


def extract_tables(cursor) -> dict:
    """Extract all user tables with columns, indexes, and foreign keys.
    
    Args:
        cursor: pyodbc cursor
        
    Returns:
        Dict mapping table name to table schema info
    """
    cursor.execute("""
        SELECT
            SCHEMA_NAME(t.schema_id) AS schema_name,
            t.name AS table_name,
            t.object_id
        FROM sys.tables t
        WHERE t.is_ms_shipped = 0
        ORDER BY t.name
    """)
    tables = cursor.fetchall()
    
    result = {}
    
    for schema_name, table_name, object_id in tables:
        full_name = f"{schema_name}.{table_name}"
        
        columns = _extract_columns(cursor, object_id)
        indexes = _extract_indexes(cursor, object_id)
        foreign_keys = _extract_foreign_keys(cursor, object_id)
        
        schema_hash = compute_table_hash(columns, indexes, foreign_keys)
        
        result[full_name] = {
            "schema": schema_name,
            "columns": columns,
            "indexes": indexes,
            "foreign_keys": foreign_keys,
            "schema_hash": schema_hash,
        }
    
    return result


def _extract_columns(cursor, object_id: int) -> list:
    """Extract columns for a table."""
    cursor.execute("""
        SELECT
            c.column_id,
            c.name AS column_name,
            tp.name AS data_type,
            c.max_length,
            c.precision,
            c.scale,
            c.is_nullable,
            c.is_identity,
            dc.definition AS default_value
        FROM sys.columns c
        JOIN sys.types tp ON c.user_type_id = tp.user_type_id
        LEFT JOIN sys.default_constraints dc
            ON dc.parent_object_id = c.object_id
            AND dc.parent_column_id = c.column_id
        WHERE c.object_id = ?
        ORDER BY c.column_id
    """, object_id)
    
    columns = []
    for row in cursor.fetchall():
        col_id, col_name, data_type, max_length, precision, scale, is_nullable, is_identity, default_value = row
        
        if data_type in ('varchar', 'nvarchar', 'char', 'nchar', 'varbinary', 'binary'):
            if max_length == -1:
                display_length = "MAX"
            elif data_type.startswith('n'):
                display_length = str(max_length // 2)
            else:
                display_length = str(max_length)
            type_str = f"{data_type}({display_length})"
        elif data_type in ('decimal', 'numeric'):
            type_str = f"{data_type}({precision},{scale})"
        elif data_type in ('float', 'real'):
            type_str = f"{data_type}({precision})"
        else:
            type_str = data_type
        
        columns.append({
            "name": col_name,
            "type": type_str,
            "nullable": bool(is_nullable),
            "identity": bool(is_identity),
            "default": default_value.strip() if default_value else None,
        })
    
    return columns


def _extract_indexes(cursor, object_id: int) -> list:
    """Extract indexes for a table (excluding primary keys)."""
    cursor.execute("""
        SELECT
            i.name AS index_name,
            i.type_desc AS index_type,
            i.is_unique,
            i.is_primary_key,
            c.name AS column_name,
            ic.key_ordinal,
            ic.is_descending_key,
            ic.is_included_column
        FROM sys.indexes i
        JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
        JOIN sys.columns c ON c.object_id = i.object_id AND c.column_id = ic.column_id
        WHERE i.object_id = ?
            AND i.type > 0
        ORDER BY i.name, ic.key_ordinal
    """, object_id)
    
    rows = cursor.fetchall()
    indexes = {}
    
    for row in rows:
        idx_name, idx_type, is_unique, is_pk, col_name, key_ordinal, is_desc, is_included = row
        
        if is_pk:
            continue
        
        if idx_name not in indexes:
            indexes[idx_name] = {
                "name": idx_name,
                "type": idx_type,
                "unique": bool(is_unique),
                "keys": [],
                "included": [],
            }
        
        direction = "DESC" if is_desc else "ASC"
        col_entry = f"[{col_name}] {direction}"
        
        if is_included:
            indexes[idx_name]["included"].append(col_name)
        else:
            indexes[idx_name]["keys"].append(col_entry)
    
    return list(indexes.values())


def _extract_foreign_keys(cursor, object_id: int) -> list:
    """Extract foreign keys for a table."""
    cursor.execute("""
        SELECT
            fk.name AS fk_name,
            c_from.name AS from_column,
            SCHEMA_NAME(t_to.schema_id) + '.' + t_to.name AS to_table,
            c_to.name AS to_column,
            fk.delete_referential_action_desc,
            fk.update_referential_action_desc
        FROM sys.foreign_keys fk
        JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
        JOIN sys.columns c_from ON c_from.object_id = fk.parent_object_id
            AND c_from.column_id = fkc.parent_column_id
        JOIN sys.tables t_to ON t_to.object_id = fk.referenced_object_id
        JOIN sys.columns c_to ON c_to.object_id = fk.referenced_object_id
            AND c_to.column_id = fkc.referenced_column_id
        WHERE fk.parent_object_id = ?
        ORDER BY fk.name
    """, object_id)
    
    foreign_keys = []
    for row in cursor.fetchall():
        fk_name, from_col, to_table, to_col, on_delete, on_update = row
        foreign_keys.append({
            "name": fk_name,
            "from": from_col,
            "to_table": to_table,
            "to_column": to_col,
            "on_delete": on_delete,
            "on_update": on_update,
        })
    
    return foreign_keys


def extract_procedures(cursor) -> dict:
    """Extract all user stored procedures.
    
    Args:
        cursor: pyodbc cursor
        
    Returns:
        Dict mapping procedure name to definition and hash
    """
    cursor.execute("""
        SELECT
            SCHEMA_NAME(p.schema_id) + '.' + p.name AS full_name,
            m.definition
        FROM sys.procedures p
        JOIN sys.sql_modules m ON p.object_id = m.object_id
        WHERE p.is_ms_shipped = 0
        ORDER BY p.name
    """)
    
    result = {}
    for full_name, definition in cursor.fetchall():
        if definition is None:
            definition = f"/* Encrypted procedure: {full_name} */"
        else:
            definition = _normalize_create_to_create_or_alter(definition)
        
        result[full_name] = {
            "definition": definition,
            "hash": compute_hash(definition),
        }
    
    return result


def extract_functions(cursor) -> dict:
    """Extract all user-defined functions.
    
    Args:
        cursor: pyodbc cursor
        
    Returns:
        Dict mapping function name to definition and hash
    """
    cursor.execute("""
        SELECT
            SCHEMA_NAME(o.schema_id) + '.' + o.name AS full_name,
            m.definition
        FROM sys.objects o
        JOIN sys.sql_modules m ON o.object_id = m.object_id
        WHERE o.is_ms_shipped = 0
            AND o.type IN ('FN', 'IF', 'TF', 'FS', 'FT')
        ORDER BY o.name
    """)
    
    result = {}
    for full_name, definition in cursor.fetchall():
        if definition is None:
            definition = f"/* Encrypted function: {full_name} */"
        else:
            definition = _normalize_create_to_create_or_alter(definition)
        
        result[full_name] = {
            "definition": definition,
            "hash": compute_hash(definition),
        }
    
    return result


def extract_triggers(cursor) -> dict:
    """Extract all triggers (DML and DDL).
    
    DML triggers (parent_class=1) are scoped to tables and get schema from parent table.
    DDL triggers (parent_class=0) are database-scoped and get 'dbo' as schema.
    
    Args:
        cursor: pyodbc cursor
        
    Returns:
        Dict mapping trigger name to definition and hash
    """
    cursor.execute("""
        SELECT
            CASE
                WHEN t.parent_class = 1 THEN SCHEMA_NAME(tab.schema_id) + '.' + t.name
                ELSE 'dbo.' + t.name
            END AS full_name,
            m.definition,
            t.parent_class_desc
        FROM sys.triggers t
        JOIN sys.sql_modules m ON t.object_id = m.object_id
        LEFT JOIN sys.tables tab ON t.parent_id = tab.object_id
        WHERE t.is_ms_shipped = 0
        ORDER BY t.name
    """)
    
    result = {}
    for full_name, definition, parent_class in cursor.fetchall():
        if full_name is None:
            continue
        if definition is None:
            definition = f"/* Encrypted trigger: {full_name} */"
        else:
            definition = _normalize_create_to_create_or_alter(definition)
        
        result[full_name] = {
            "definition": definition,
            "hash": compute_hash(definition),
            "parent_class": parent_class,
        }
    
    return result


def _normalize_create_to_create_or_alter(definition: str) -> str:
    """Replace CREATE PROCEDURE/FUNCTION/TRIGGER with CREATE OR ALTER.
    
    This ensures the definition can be run directly on any SQL Server
    without needing to check if the object already exists.
    """
    pattern = re.compile(
        r'\b(CREATE)\s+(OR\s+ALTER\s+)?(PROC(?:EDURE)?|FUNCTION|TRIGGER|VIEW)\b',
        re.IGNORECASE
    )
    return pattern.sub(r'CREATE OR ALTER \3', definition, count=1)


def extract_full_schema(conn_str: str, driver: str = None, trust_cert: bool = False, show_progress: bool = True) -> dict:
    """Extract the full schema from a SQL Server database.
    
    Args:
        conn_str: Connection string
        driver: ODBC driver name (auto-detected if None)
        trust_cert: Whether to trust self-signed certificates
        show_progress: Whether to show progress bars
        
    Returns:
        Dict with tables, procedures, functions, triggers
    """
    conn = connect_to_db(conn_str, driver, trust_cert)
    cursor = conn.cursor()
    
    try:
        cursor.execute("SET NOCOUNT ON")
        cursor.execute("SELECT @@SERVERNAME, DB_NAME()")
        server, database = cursor.fetchone()
        
        result = {}
        
        if show_progress:
            print("Extracting tables...")
        try:
            result["tables"] = extract_tables(cursor)
        except Exception as e:
            print(f"Warning: Failed to extract tables: {e}")
            result["tables"] = {}
        
        if show_progress:
            print(f"Extracting procedures ({len(result['tables'])} tables found)...")
        try:
            result["procedures"] = extract_procedures(cursor)
        except Exception as e:
            print(f"Warning: Failed to extract procedures: {e}")
            result["procedures"] = {}
        
        if show_progress:
            print(f"Extracting functions ({len(result['procedures'])} procedures found)...")
        try:
            result["functions"] = extract_functions(cursor)
        except Exception as e:
            print(f"Warning: Failed to extract functions: {e}")
            result["functions"] = {}
        
        if show_progress:
            print(f"Extracting triggers ({len(result['functions'])} functions found)...")
        try:
            result["triggers"] = extract_triggers(cursor)
        except Exception as e:
            print(f"Warning: Failed to extract triggers: {e}")
            result["triggers"] = {}
        
        result["_meta"] = {
            "server": server,
            "database": database,
        }
        
        return result
    finally:
        conn.close()
