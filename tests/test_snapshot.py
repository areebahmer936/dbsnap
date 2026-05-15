"""Tests for snapshot module."""

import os
import json
import tempfile
import zstandard as zstd

from dbsnap.snapshot import (
    create_snapshot,
    save_snapshot,
    load_snapshot,
    get_snapshot_info,
    MAGIC_HEADER,
)


SAMPLE_EXTRACTED = {
    "tables": {
        "Users": {
            "schema": "dbo",
            "columns": [{"name": "Id", "type": "int", "nullable": False}],
            "indexes": [],
            "foreign_keys": [],
            "schema_hash": "abc123",
        }
    },
    "procedures": {
        "dbo.GetUser": {
            "definition": "CREATE PROCEDURE dbo.GetUser AS SELECT * FROM Users",
            "hash": "def456",
        }
    },
    "functions": {},
    "triggers": {},
    "_meta": {
        "server": "localhost",
        "database": "TestDB",
    },
}


class TestCreateSnapshot:
    def test_creates_meta(self):
        snap = create_snapshot(SAMPLE_EXTRACTED)
        assert "meta" in snap
        assert "tool_version" in snap["meta"]
        assert "created_at" in snap["meta"]
        assert snap["meta"]["server"] == "localhost"
        assert snap["meta"]["database"] == "TestDB"

    def test_includes_all_categories(self):
        snap = create_snapshot(SAMPLE_EXTRACTED)
        assert "tables" in snap
        assert "procedures" in snap
        assert "functions" in snap
        assert "triggers" in snap

    def test_overrides_meta(self):
        snap = create_snapshot(SAMPLE_EXTRACTED, server="prod", database="ProdDB")
        assert snap["meta"]["server"] == "prod"
        assert snap["meta"]["database"] == "ProdDB"


class TestSaveAndLoadSnapshot:
    def test_roundtrip(self):
        snap = create_snapshot(SAMPLE_EXTRACTED)
        
        with tempfile.NamedTemporaryFile(suffix=".dbsnap", delete=False) as f:
            filepath = f.name
        
        try:
            save_snapshot(snap, filepath)
            loaded = load_snapshot(filepath)
            
            assert loaded["meta"]["server"] == snap["meta"]["server"]
            assert loaded["meta"]["database"] == snap["meta"]["database"]
            assert loaded["tables"] == snap["tables"]
            assert loaded["procedures"] == snap["procedures"]
        finally:
            os.unlink(filepath)

    def test_file_has_magic_header(self):
        snap = create_snapshot(SAMPLE_EXTRACTED)
        
        with tempfile.NamedTemporaryFile(suffix=".dbsnap", delete=False) as f:
            filepath = f.name
        
        try:
            save_snapshot(snap, filepath)
            
            with open(filepath, 'rb') as f:
                header = f.read(len(MAGIC_HEADER))
            assert header == MAGIC_HEADER
        finally:
            os.unlink(filepath)

    def test_file_is_compressed(self):
        snap = create_snapshot(SAMPLE_EXTRACTED)
        
        with tempfile.NamedTemporaryFile(suffix=".dbsnap", delete=False) as f:
            filepath = f.name
        
        try:
            save_snapshot(snap, filepath)
            
            json_size = len(json.dumps(snap, indent=2).encode('utf-8'))
            file_size = os.path.getsize(filepath)
            
            assert file_size < json_size
        finally:
            os.unlink(filepath)

    def test_load_nonexistent_file(self):
        import pytest
        with pytest.raises(FileNotFoundError):
            load_snapshot("/nonexistent/path.dbsnap")

    def test_load_invalid_file(self):
        import pytest
        with tempfile.NamedTemporaryFile(suffix=".dbsnap", delete=False) as f:
            filepath = f.name
            f.write(b"not a dbsnap file")
        
        try:
            with pytest.raises(ValueError):
                load_snapshot(filepath)
        finally:
            os.unlink(filepath)


class TestGetSnapshotInfo:
    def test_returns_summary(self):
        snap = create_snapshot(SAMPLE_EXTRACTED)
        
        with tempfile.NamedTemporaryFile(suffix=".dbsnap", delete=False) as f:
            filepath = f.name
        
        try:
            save_snapshot(snap, filepath)
            info = get_snapshot_info(filepath)
            
            assert info["table_count"] == 1
            assert info["procedure_count"] == 1
            assert info["function_count"] == 0
            assert info["trigger_count"] == 0
            assert info["server"] == "localhost"
            assert info["database"] == "TestDB"
            assert info["file_size"] > 0
        finally:
            os.unlink(filepath)
