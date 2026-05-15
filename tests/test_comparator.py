"""Tests for comparator module."""

from dbsnap.comparator import (
    DiffItem,
    compare_snapshots,
    compute_diffs,
    compare_all_categories,
    get_summary,
    filter_items,
)


LEFT_SNAPSHOT = {
    "tables": {
        "Users": {
            "columns": [{"name": "Id", "type": "int", "nullable": False}],
            "indexes": [],
            "foreign_keys": [],
            "schema_hash": "hash1",
        },
        "Orders": {
            "columns": [{"name": "Id", "type": "int", "nullable": False}],
            "indexes": [],
            "foreign_keys": [],
            "schema_hash": "hash2",
        },
    },
    "procedures": {
        "dbo.GetUser": {
            "definition": "CREATE PROC dbo.GetUser AS SELECT * FROM Users",
            "hash": "proc_hash1",
        },
        "dbo.DeleteUser": {
            "definition": "CREATE PROC dbo.DeleteUser AS DELETE FROM Users",
            "hash": "proc_hash2",
        },
    },
    "functions": {},
    "triggers": {},
}


RIGHT_SNAPSHOT = {
    "tables": {
        "Users": {
            "columns": [{"name": "Id", "type": "int", "nullable": False}, {"name": "Name", "type": "nvarchar(100)", "nullable": True}],
            "indexes": [],
            "foreign_keys": [],
            "schema_hash": "hash1_modified",
        },
        "Products": {
            "columns": [{"name": "Id", "type": "int", "nullable": False}],
            "indexes": [],
            "foreign_keys": [],
            "schema_hash": "hash3",
        },
    },
    "procedures": {
        "dbo.GetUser": {
            "definition": "CREATE PROC dbo.GetUser AS SELECT Id, Name FROM Users",
            "hash": "proc_hash1_modified",
        },
        "dbo.DeleteUser": {
            "definition": "CREATE PROC dbo.DeleteUser AS DELETE FROM Users",
            "hash": "proc_hash2",
        },
    },
    "functions": {},
    "triggers": {},
}


class TestCompareSnapshots:
    def test_identical_items(self):
        items = compare_snapshots(LEFT_SNAPSHOT, RIGHT_SNAPSHOT, "procedures")
        identical = [i for i in items if i.status == "identical"]
        assert len(identical) == 1
        assert identical[0].name == "dbo.DeleteUser"

    def test_modified_items(self):
        items = compare_snapshots(LEFT_SNAPSHOT, RIGHT_SNAPSHOT, "procedures")
        modified = [i for i in items if i.status == "modified"]
        assert len(modified) == 1
        assert modified[0].name == "dbo.GetUser"

    def test_only_in_left(self):
        items = compare_snapshots(LEFT_SNAPSHOT, RIGHT_SNAPSHOT, "tables")
        only_left = [i for i in items if i.status == "only_in_left"]
        assert len(only_left) == 1
        assert only_left[0].name == "Orders"

    def test_only_in_right(self):
        items = compare_snapshots(LEFT_SNAPSHOT, RIGHT_SNAPSHOT, "tables")
        only_right = [i for i in items if i.status == "only_in_right"]
        assert len(only_right) == 1
        assert only_right[0].name == "Products"

    def test_sorted_by_name(self):
        items = compare_snapshots(LEFT_SNAPSHOT, RIGHT_SNAPSHOT, "tables")
        names = [i.name for i in items]
        assert names == sorted(names)


class TestComputeDiffs:
    def test_modified_gets_diff(self):
        items = compare_snapshots(LEFT_SNAPSHOT, RIGHT_SNAPSHOT, "procedures")
        items = compute_diffs(items)
        modified = [i for i in items if i.status == "modified"]
        assert len(modified) == 1
        assert len(modified[0].unified_diff) > 0

    def test_only_in_left_gets_diff(self):
        items = compare_snapshots(LEFT_SNAPSHOT, RIGHT_SNAPSHOT, "tables")
        items = compute_diffs(items)
        only_left = [i for i in items if i.status == "only_in_left"]
        assert len(only_left[0].unified_diff) > 0

    def test_only_in_right_gets_diff(self):
        items = compare_snapshots(LEFT_SNAPSHOT, RIGHT_SNAPSHOT, "tables")
        items = compute_diffs(items)
        only_right = [i for i in items if i.status == "only_in_right"]
        assert len(only_right[0].unified_diff) > 0


class TestGetSummary:
    def test_correct_counts(self):
        comparison = compare_all_categories(LEFT_SNAPSHOT, RIGHT_SNAPSHOT)
        summary = get_summary(comparison)
        
        assert summary["tables"]["modified"] == 1
        assert summary["tables"]["only_in_left"] == 1
        assert summary["tables"]["only_in_right"] == 1
        assert summary["procedures"]["modified"] == 1
        assert summary["procedures"]["identical"] == 1


class TestFilterItems:
    def test_exclude_identical(self):
        items = compare_snapshots(LEFT_SNAPSHOT, RIGHT_SNAPSHOT, "procedures")
        filtered = filter_items(items, exclude_identical=True)
        assert all(i.status != "identical" for i in filtered)
        assert len(filtered) < len(items)

    def test_include_all(self):
        items = compare_snapshots(LEFT_SNAPSHOT, RIGHT_SNAPSHOT, "procedures")
        filtered = filter_items(items, exclude_identical=False)
        assert len(filtered) == len(items)
