# dbsnap - SQL Server Schema Snapshot & Diff Tool

A CLI tool to snapshot SQL Server database schemas into portable `.dbsnap` files and produce rich HTML diff reports comparing any two snapshots (or a snapshot vs a live database).

## Features

- **Snapshot**: Export full schema (tables, columns, indexes, foreign keys, stored procedures, functions, triggers) into a single compressed `.dbsnap` file
- **Compare**: Fast hash-first comparison engine with O(n) dictionary lookups
- **HTML Report**: Self-contained, offline-viewable diff report with tabs, filter chips, search, and inline diffs
- **Portable**: Work entirely offline once snapshots are taken — no server connection needed for comparison

## Installation

### Prerequisites

- Python 3.9 or higher
- ODBC Driver for SQL Server:
  - **Windows**: [ODBC Driver 18 for SQL Server](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server)
  - **macOS**: `brew install msodbcsql18`
  - **Linux**: Follow [Microsoft's guide](https://learn.microsoft.com/en-us/sql/connect/odbc/linux-mac/installing-the-microsoft-odbc-driver-for-sql-server)

### Install from source

```bash
git clone <repo-url>
cd dbsnap
pip install -e .
```

### Install with pip

```bash
pip install dbsnap
```

## Usage

### Take a snapshot

```bash
dbsnap snapshot --conn "SERVER=localhost;DATABASE=mydb;UID=sa;PWD=yourpassword" --out devDb.dbsnap
```

### Compare two snapshots

```bash
dbsnap compare devDb.dbsnap prodDb.dbsnap --out diff.html
```

### Compare snapshot vs live database

```bash
dbsnap compare devDb.dbsnap --conn "SERVER=prod;DATABASE=mydb;UID=sa;PWD=yourpassword" --out diff.html
```

### View snapshot info

```bash
dbsnap info devDb.dbsnap
```

### Filter options

```bash
# Exclude identical items
dbsnap compare devDb.dbsnap prodDb.dbsnap --out diff.html --no-identical

# Compare only tables
dbsnap compare devDb.dbsnap prodDb.dbsnap --out diff.html --schema-only

# Compare only stored procedures
dbsnap compare devDb.dbsnap prodDb.dbsnap --out diff.html --procs-only

# Compare only functions
dbsnap compare devDb.dbsnap prodDb.dbsnap --out diff.html --functions-only

# Compare only triggers
dbsnap compare devDb.dbsnap prodDb.dbsnap --out diff.html --triggers-only
```

### Connection options

```bash
# Specify ODBC driver
dbsnap snapshot --conn "SERVER=localhost;DATABASE=mydb;UID=sa;PWD=..." --out devDb.dbsnap --driver "ODBC Driver 17 for SQL Server"

# Trust self-signed certificates
dbsnap snapshot --conn "SERVER=localhost;DATABASE=mydb;UID=sa;PWD=..." --out devDb.dbsnap --trust-server-cert
```

## The `.dbsnap` File Format

Internally: **JSON compressed with zstandard**. The file is:
- Inspectable (decompress + open in any editor)
- Highly compressible (SQL text is repetitive)
- Portable (no binary format, no version-locked serialization)

## HTML Report Features

- **Tabs**: Schema, Procedures, Functions, Triggers
- **Filter chips**: All / Modified / Only in left / Only in right / Identical
- **Search**: Real-time filtering as you type
- **Expand to diff**: Click any item to reveal the line-level diff
- **Legend**: Red = left DB, Green = right DB
- **Self-contained**: One `.html` file, works offline

## Development

### Run tests

```bash
pip install pytest
pytest tests/
```

### Project structure

```
dbsnap/
├── dbsnap/
│   ├── __init__.py
│   ├── cli.py            # Click CLI entry point
│   ├── extractor.py      # pyodbc queries
│   ├── snapshot.py       # .dbsnap serialization
│   ├── hasher.py         # Normalization + SHA-256
│   ├── comparator.py     # Hash comparison engine
│   ├── reporter.py       # HTML report generation
│   └── templates/
│       └── report.html   # Jinja2 HTML template
├── tests/
├── pyproject.toml
└── requirements.txt
```

## License

MIT
