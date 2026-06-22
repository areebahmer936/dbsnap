"""Snapshot serialization and deserialization for dbsnap."""

import io
import json
import os
import threading
import time
from datetime import datetime, timezone
import zstandard as zstd
import ijson
from tqdm import tqdm

from . import __version__

MAGIC_HEADER = b"dbsnap\x00\x02"


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


def _deduplicate_definitions(snapshot: dict) -> dict:
    """Replace definition strings with indices into a shared pool.
    
    Returns a new dict with a 'defs' array and definitions replaced by integers.
    The data section is passed by reference to avoid copying large datasets.
    """
    defs = []
    def_index = {}
    
    def get_idx(definition):
        if definition in def_index:
            return def_index[definition]
        idx = len(defs)
        defs.append(definition)
        def_index[definition] = idx
        return idx
    
    result = {"meta": snapshot["meta"], "defs": defs}
    
    for category in ("tables", "procedures", "functions", "triggers"):
        items = snapshot.get(category, {})
        result[category] = {}
        for name, data in items.items():
            new_data = {}
            for key, value in data.items():
                if key == "definition" and isinstance(value, str):
                    new_data[key] = get_idx(value)
                else:
                    new_data[key] = value
            result[category][name] = new_data
    
    if "data" in snapshot:
        result["data"] = snapshot["data"]
    
    return result


def _restore_definitions(compressed: dict) -> dict:
    """Restore definition strings from the shared pool."""
    defs = compressed.get("defs", [])
    if not defs:
        return compressed
    
    result = {"meta": compressed["meta"]}
    
    for category in ("tables", "procedures", "functions", "triggers"):
        items = compressed.get(category, {})
        result[category] = {}
        for name, data in items.items():
            new_data = {}
            for key, value in data.items():
                if key == "definition" and isinstance(value, int):
                    new_data[key] = defs[value] if value < len(defs) else ""
                else:
                    new_data[key] = value
            result[category][name] = new_data
    
    if "data" in compressed:
        result["data"] = compressed["data"]
    
    return result


def load_snapshot_schema(filepath: str) -> dict:
    """Load a snapshot's metadata and schema without loading the data section.

    Uses ijson to stream-parse the JSON at event level. Stops parsing
    as soon as the 'data' key is encountered at the root level, before
    its value is ever loaded into memory.

    Args:
        filepath: Path to the .dbsnap file

    Returns:
        Snapshot dict without 'data' key
    """
    dctx = zstd.ZstdDecompressor()
    with open(filepath, 'rb') as f:
        if f.read(len(MAGIC_HEADER)) != MAGIC_HEADER:
            raise ValueError(f"Invalid .dbsnap file format: {filepath}")
        with dctx.stream_reader(f) as reader:
            text_reader = io.TextIOWrapper(reader, encoding='utf-8')
            result = _parse_until_data(text_reader)

    return _restore_definitions(result)


def _parse_until_data(stream):
    """Parse JSON from stream, building full dict until 'data' key is reached at root.

    Uses a recursive descent approach on ijson events. When a root-level
    'data' map_key is encountered, parsing stops immediately.
    """
    events = ijson.parse(stream, use_float=True)
    return _parse_value(events, depth=0)


def _parse_value(events, depth):
    """Parse a single JSON value from the event stream and return it.
    
    Stops at root level if 'data' map_key is encountered.
    """
    for prefix, event, value in events:
        if event == 'start_map':
            return _parse_map(events, depth + 1)
        elif event == 'start_array':
            return _parse_array(events, depth + 1)
        elif event == 'string':
            return value
        elif event == 'number':
            return value
        elif event == 'boolean':
            return value
        elif event == 'null':
            return None
        elif event == 'end_map' or event == 'end_array':
            return value
        elif event == 'map_key':
            continue
    return None


def _parse_map(events, depth):
    """Parse a JSON object from the event stream."""
    result = {}
    for prefix, event, value in events:
        if event == 'map_key':
            key = value
            # At root level, stop when we hit 'data'
            if depth == 1 and key == 'data':
                # Yield a synthetic end_map to close the root object
                return result
            result[key] = _parse_value(events, depth)
        elif event == 'end_map':
            return result
    return result


def _parse_array(events, depth):
    """Parse a JSON array from the event stream."""
    result = []
    for prefix, event, value in events:
        if event == 'end_array':
            return result
        else:
            # Push back non-end events as start of a value
            val = _parse_value_from_event(event, value, events, depth)
            result.append(val)
    return result


def _parse_value_from_event(event, value, events, depth):
    """Parse a value starting from an already-consumed event."""
    if event == 'start_map':
        return _parse_map(events, depth + 1)
    elif event == 'start_array':
        return _parse_array(events, depth + 1)
    elif event == 'string':
        return value
    elif event == 'number':
        return value
    elif event == 'boolean':
        return value
    elif event == 'null':
        return None
    elif event == 'map_key':
        key = value
        # At root level, stop on data
        if depth == 1 and key == 'data':
            return None
        return _parse_value(events, depth)
    return None


def stream_data_tables(filepath: str):
    """Yield (table_name, rows_list) from the data section.

    Re-reads the file from the start, decompresses, and uses
    ijson to parse only the 'data' section. Each table's rows are
    materialized into a list for the caller, then released.

    Args:
        filepath: Path to the .dbsnap file

    Yields:
        Tuple of (table_name, list_of_row_dicts)
    """
    dctx = zstd.ZstdDecompressor()
    with open(filepath, 'rb') as f:
        if f.read(len(MAGIC_HEADER)) != MAGIC_HEADER:
            raise ValueError(f"Invalid .dbsnap file format: {filepath}")
        with dctx.stream_reader(f) as reader:
            text_reader = io.TextIOWrapper(reader, encoding='utf-8')
            for table_name, rows in ijson.kvitems(text_reader, 'data'):
                yield table_name, list(rows)


def save_snapshot(snapshot: dict, filepath: str, compress_level: int = None) -> str:
    """Save a snapshot to a compressed .dbsnap file.

    Uses streaming write: JSON is piped directly into zstd compression
    and written to disk, avoiding multiple copies in memory.

    Auto-selects compression level: 19 for schema-only (small, maximize ratio),
    level 6 for data-heavy snapshots.

    Args:
        snapshot: Snapshot dict from create_snapshot
        filepath: Output file path
        compress_level: Force a specific zstd compression level (1-19)

    Returns:
        Path to the saved file
    """
    has_data = "data" in snapshot and snapshot["data"]
    if compress_level is None:
        compress_level = 6 if has_data else 19

    deduped = _deduplicate_definitions(snapshot)

    data_rows = snapshot.get("meta", {}).get("data_row_count", 0)
    desc = f"Compressing {data_rows:,} rows" if data_rows else "Compressing"

    cctx = zstd.ZstdCompressor(level=compress_level)
    done = threading.Event()

    with tqdm(desc=desc, unit="B", unit_scale=True, unit_divisor=1024,
              bar_format="{desc}: {elapsed} elapsed | {rate_fmt}") as pbar:

        def heartbeat():
            while not done.is_set():
                time.sleep(0.25)
                pbar.refresh()

        t = threading.Thread(target=heartbeat, daemon=True)
        t.start()

        try:
            with open(filepath, 'wb') as f:
                f.write(MAGIC_HEADER)
                with cctx.stream_writer(f) as cw:
                    text_wrapper = io.TextIOWrapper(cw, encoding='utf-8')
                    json.dump(deduped, text_wrapper, separators=(',', ':'), ensure_ascii=False)
                    text_wrapper.detach()
        finally:
            done.set()
            t.join(timeout=0.5)

    return filepath


def load_snapshot(filepath: str) -> dict:
    """Load a snapshot from a .dbsnap file.
    
    Uses streaming decompression to avoid holding the full compressed
    and uncompressed data in memory simultaneously.
    
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
    
    dctx = zstd.ZstdDecompressor()
    with open(filepath, 'rb') as f:
        if f.read(len(MAGIC_HEADER)) != MAGIC_HEADER:
            raise ValueError(f"Invalid .dbsnap file format: {filepath}")
        with dctx.stream_reader(f) as reader:
            text_reader = io.TextIOWrapper(reader, encoding='utf-8')
            raw = json.load(text_reader)
    
    if "meta" not in raw or "tables" not in raw:
        raise ValueError(f"Corrupted snapshot file: {filepath}")
    
    return _restore_definitions(raw)


def get_snapshot_info(filepath: str) -> dict:
    """Get summary information about a snapshot file.

    Uses streaming schema load to avoid loading data into memory.
    For data row count, reads from metadata (set during snapshot creation).

    Args:
        filepath: Path to the .dbsnap file

    Returns:
        Dict with metadata and counts
    """
    snapshot = load_snapshot_schema(filepath)

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
        "data_rows": meta.get("data_row_count", 0),
        "file_size": os.path.getsize(filepath),
    }
