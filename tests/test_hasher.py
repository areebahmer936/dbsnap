"""Tests for hasher module."""

from dbsnap.hasher import normalize_definition, compute_hash, compute_table_hash


class TestNormalizeDefinition:
    def test_strips_whitespace(self):
        assert normalize_definition("SELECT  *  FROM  users") == "select*fromusers"

    def test_strips_newlines(self):
        assert normalize_definition("SELECT\n*\nFROM\nusers") == "select*fromusers"

    def test_strips_tabs(self):
        assert normalize_definition("SELECT\t*\tFROM\tusers") == "select*fromusers"

    def test_lowercases(self):
        assert normalize_definition("SELECT * FROM Users") == "select*fromusers"

    def test_empty_string(self):
        assert normalize_definition("") == ""

    def test_none(self):
        assert normalize_definition(None) == ""

    def test_cosmetic_changes_produce_same_hash(self):
        original = "SELECT * FROM users WHERE id = 1"
        reformatted = """
            SELECT
                *
            FROM
                users
            WHERE
                id = 1
        """
        assert normalize_definition(original) == normalize_definition(reformatted)


class TestComputeHash:
    def test_deterministic(self):
        h1 = compute_hash("SELECT * FROM users")
        h2 = compute_hash("SELECT * FROM users")
        assert h1 == h2

    def test_different_inputs_different_hashes(self):
        h1 = compute_hash("SELECT * FROM users")
        h2 = compute_hash("SELECT * FROM orders")
        assert h1 != h2

    def test_cosmetic_changes_same_hash(self):
        original = "SELECT * FROM users WHERE id = 1"
        reformatted = "  SELECT  *  FROM  users  WHERE  id  =  1  "
        assert compute_hash(original) == compute_hash(reformatted)

    def test_case_insensitive(self):
        assert compute_hash("SELECT * FROM users") == compute_hash("select * from users")


class TestComputeTableHash:
    def test_same_columns_same_hash(self):
        columns1 = [{"name": "id", "type": "int", "nullable": False, "identity": True, "default": None}]
        columns2 = [{"name": "id", "type": "int", "nullable": False, "identity": True, "default": None}]
        
        h1 = compute_table_hash(columns1, [], [])
        h2 = compute_table_hash(columns2, [], [])
        assert h1 == h2

    def test_different_columns_different_hash(self):
        columns1 = [{"name": "id", "type": "int", "nullable": False, "identity": True, "default": None}]
        columns2 = [{"name": "id", "type": "int", "nullable": False, "identity": True, "default": None},
                    {"name": "name", "type": "nvarchar(100)", "nullable": True, "identity": False, "default": None}]
        
        h1 = compute_table_hash(columns1, [], [])
        h2 = compute_table_hash(columns2, [], [])
        assert h1 != h2

    def test_order_independent(self):
        columns1 = [
            {"name": "id", "type": "int", "nullable": False, "identity": True, "default": None},
            {"name": "name", "type": "nvarchar(100)", "nullable": True, "identity": False, "default": None},
        ]
        columns2 = [
            {"name": "name", "type": "nvarchar(100)", "nullable": True, "identity": False, "default": None},
            {"name": "id", "type": "int", "nullable": False, "identity": True, "default": None},
        ]
        
        h1 = compute_table_hash(columns1, [], [])
        h2 = compute_table_hash(columns2, [], [])
        assert h1 == h2
