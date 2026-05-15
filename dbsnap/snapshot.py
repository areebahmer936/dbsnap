"""Snapshot serialization and deserialization for dbsnap."""

import json
import os
from datetime import datetime, timezone
import zstandard as zstd

from . import __version__

MAGIC_HEADER = b"dbsnap\x00\x01"


def create_snapshot(extracted_data: dict, server: str = None, database: str = None) -> dict:
    """Create a complete snapshot dict from extracted data.
    
    Args:
        extracted_data: Dict from extract_full_schema
        server: Server name (overrides extracted metadata)
        database: Database name (overrides extracted metadata)
        
    Returns:
        Complete snapshot dict ready for serialization
    """
    meta = extracted_data.get("_meta", {})
    
    snapshot = {
        "meta": {
            "tool_version": __version__,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "server": server or meta.get("server", "unknown"),
            "database": database or meta.get("database", "unknown"),
        },
        "tables": extracted_data.get("tables", {}),
        "procedures": extracted_data.get("procedures", {}),
        "functions": extracted_data.get("functions", {}),
        "triggers": extracted_data.get("triggers", {}),
    }
    
    return snapshot


def save_snapshot(snapshot: dict, filepath: str) -> str:
    """Save a snapshot to a compressed .dbsnap file.
    
    Args:
        snapshot: Snapshot dict from create_snapshot
        filepath: Output file path
        
    Returns:
        Path to the saved file
    """
    json_data = json.dumps(snapshot, indent=2, ensure_ascii=False).encode('utf-8')
    
    compressor = zstd.ZstdCompressor(level=10)
    compressed = compressor.compress(json_data)
    
    with open(filepath, 'wb') as f:
        f.write(MAGIC_HEADER)
        f.write(compressed)
    
    file_size = os.path.getsize(filepath)
    return filepath


def load_snapshot(filepath: str) -> dict:
    """Load a snapshot from a .dbsnap file.
    
    Args:
        filepath: Path to the .dbsnap file
        
    Returns:
        Snapshot dict
        
    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If file format is invalid
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Snapshot file not found: {filepath}")
    
    with open(filepath, 'rb') as f:
        header = f.read(len(MAGIC_HEADER))
        if header != MAGIC_HEADER:
            raise ValueError(f"Invalid .dbsnap file format: {filepath}")
        
        compressed = f.read()
    
    decompressor = zstd.ZstdDecompressor()
    json_data = decompressor.decompress(compressed)
    
    snapshot = json.loads(json_data.decode('utf-8'))
    
    if "meta" not in snapshot or "tables" not in snapshot:
        raise ValueError(f"Corrupted snapshot file: {filepath}")
    
    return snapshot


def get_snapshot_info(filepath: str) -> dict:
    """Get summary information about a snapshot file.
    
    Args:
        filepath: Path to the .dbsnap file
        
    Returns:
        Dict with metadata and counts
    """
    snapshot = load_snapshot(filepath)
    
    meta = snapshot.get("meta", {})
    
    return {
        "tool_version": meta.get("tool_version", "unknown"),
        "created_at": meta.get("created_at", "unknown"),
        "server": meta.get("server", "unknown"),
        "database": meta.get("database", "unknown"),
        "table_count": len(snapshot.get("tables", {})),
        "procedure_count": len(snapshot.get("procedures", {})),
        "function_count": len(snapshot.get("functions", {})),
        "trigger_count": len(snapshot.get("triggers", {})),
        "file_size": os.path.getsize(filepath),
    }
