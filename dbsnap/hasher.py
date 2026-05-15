"""Hashing utilities for dbsnap snapshots."""

import hashlib
import re


def normalize_definition(definition: str) -> str:
    """Normalize a SQL definition by stripping whitespace and lowercasing.
    
    This ensures cosmetic reformatting (extra blank lines, indentation changes)
    does not register as a change.
    """
    if not definition:
        return ""
    normalized = re.sub(r'\s+', '', definition)
    return normalized.lower()


def compute_hash(definition: str) -> str:
    """Compute SHA-256 hash of a normalized SQL definition.
    
    Args:
        definition: Raw SQL definition string
        
    Returns:
        Hex digest of the SHA-256 hash
    """
    normalized = normalize_definition(definition)
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def compute_table_hash(columns: list, indexes: list, foreign_keys: list) -> str:
    """Compute SHA-256 hash for a table schema.
    
    Hash covers the normalized concatenation of:
    - Column names + types + nullability + identity + defaults
    - Index definitions (name, type, unique, keys, includes)
    - Foreign key definitions (name, from, to_table, to_column, on_delete, on_update)
    
    Args:
        columns: List of column dicts
        indexes: List of index dicts
        foreign_keys: List of foreign key dicts
        
    Returns:
        Hex digest of the SHA-256 hash
    """
    parts = []
    
    for col in sorted(columns, key=lambda c: c.get('name', '')):
        parts.append(f"col:{col.get('name','')}:{col.get('type','')}:{col.get('nullable','')}:{col.get('identity','')}:{col.get('default','')}")
    
    for idx in sorted(indexes, key=lambda i: i.get('name', '')):
        keys_str = ','.join(idx.get('keys', []))
        included_str = ','.join(idx.get('included', []))
        parts.append(f"idx:{idx.get('name','')}:{idx.get('type','')}:{idx.get('unique','')}:{keys_str}:{included_str}")
    
    for fk in sorted(foreign_keys, key=lambda f: f.get('name', '')):
        parts.append(f"fk:{fk.get('name','')}:{fk.get('from','')}:{fk.get('to_table','')}:{fk.get('to_column','')}:{fk.get('on_delete','')}:{fk.get('on_update','')}")
    
    combined = '|'.join(parts)
    normalized = re.sub(r'\s+', '', combined).lower()
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()
