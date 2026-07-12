# genro-sqlmigration

**Database schema migration engine** — compare a desired database
structure against a live database and generate (or apply) the SQL
commands that align them.

ORM-agnostic by design: the library never reads your model. You
describe the database in a normalized JSON contract — directly, or
through the friendlier **human JSON** / **XML** external formats — and
the engine does the rest: introspection, diff, DDL generation,
execution.

> Status: **Alpha** — API may change. Not yet published on PyPI.

## Features

- **Four dialects**: PostgreSQL (primary), SQLite, MySQL, MSSQL — one
  reader/writer/adapter trio per dialect, capability-gated.
- **Two equivalent external formats**: human JSON and XSD-validated
  XML; the same model in either compiles to the identical internal
  structure.
- **Deterministic entity matching**: FK/UNIQUE/index names are
  structural hashes computed from schema + table + columns, so
  renaming never causes spurious diffs.
- **Safe by default**: destructive commands (DROP) are disabled unless
  explicitly enabled; type conversions can be forced and backed up.
- **Minimal runtime dependencies**: `dictdiffer`, plus the DB driver
  of your dialect as an optional extra.

## Install

Not yet on PyPI — install from GitHub:

```bash
pip install "genro-sqlmigration[postgresql] @ git+https://github.com/genropy/genro-sqlmigration"
```

Extras: `postgresql` (psycopg 3), `mysql` (PyMySQL), `mssql`
(pymssql), `validation` (jsonschema), `docs`, `dev`, `all`.
SQLite needs no extra (stdlib driver).

## Quickstart

```python
from genro_sqlmigration import (
    JsonStructureProducer, PgDatabase, SqlMigrator, StructureValidator,
)

model = {
    "db": "mydb",
    "schemas": [
        {"name": "public", "tables": [
            {"name": "author", "pkey": "id", "columns": [
                {"name": "id", "dtype": "serial"},
                {"name": "name", "dtype": "A", "size": "0:120",
                 "notnull": True},
            ]},
        ]},
    ],
}

structure = JsonStructureProducer(model).get_json_struct()

db = PgDatabase({"dbname": "mydb", "host": "localhost"},
                application_schemas=["public"])
migrator = SqlMigrator(db)
migrator.ormStructure = StructureValidator().validate(structure)
migrator.prepareMigrationCommands()
print(migrator.getChanges())        # review the SQL...
# migrator.applyChanges()           # ...or apply it
```

Swap `PgDatabase` for `SqliteDatabase`, `MysqlDatabase` or
`MssqlDatabase` for the other dialects.

## Describing the database

Three input formats, from the friendliest to the most technical:

1. **Human JSON** — readable JSON with names and lists, compiled by
   `JsonStructureProducer` (above).
2. **XML** — the same information validated by the packaged XSD
   (`sql_model-1.0.xsd`), ideal for editors and GUIs; compiled by
   `XmlStructureProducer`. The inverse `struct_to_xml()` turns an
   introspected database back into editable XML.
3. **Normalized JSON** — the internal contract, built directly with
   the `new_*_item` factories (the route an ORM extractor takes).

The complete format reference — columns, dtypes, foreign keys
(including multi-column), constraints, indexes with per-column sort
order and WITH options, extensions, event triggers — is in the
[Producer Guide](https://genro-sqlmigration.readthedocs.io), also
available as [docs/producer_guide.md](docs/producer_guide.md).

## How it works

```text
your model ──(producer)──► normalized JSON ──┐
                                             ├──► diff ──► SQL commands ──► apply
live database ──(reader)──► normalized JSON ─┘
```

Both sides project to the same normalized JSON (hierarchy:
`root → schemas → tables → columns/relations/constraints/indexes`,
each entity carrying `entity`, `entity_name`, `attributes`). The diff
engine (`dictdiffer`) emits added/changed/removed events; the command
builder turns them into dialect-specific DDL; the executor assembles
and applies it. The JSON Schema of the contract is packaged as
`structure-1.0.json`.

## Modules

| Module | Responsibility |
| --- | --- |
| `structures.py` | Contract constants, entity factories, name hashing |
| `validation.py` | `StructureValidator` — JSON Schema + semantic checks |
| `json_producer.py` | Human JSON → normalized JSON |
| `xml_producer.py` | XML → normalized JSON, and back (`struct_to_xml`) |
| `editor_service.py` | DB ↔ XML round-trip service for editors |
| `diff_engine.py` | Structure comparison → typed events |
| `command_builder.py` | Events → SQL command fragments |
| `executor.py` | SQL assembly, execution, backup verification |
| `migrator.py` | `SqlMigrator` — the orchestrator |
| `database.py` | Abstract `Database` / adapter interfaces |
| `readers/` | Per-dialect introspection (live DB → normalized JSON) |
| `writers/` | Per-dialect DDL generation |
| `adapters/` | Concrete `Database`/adapter pairs per dialect |

## Documentation

Full documentation on
[Read the Docs](https://genro-sqlmigration.readthedocs.io).
Design notes and milestones live in [roadmap/](roadmap/).

## License

Apache License 2.0 — Copyright Softwell S.r.l.
See [LICENSE](LICENSE) and [NOTICE](NOTICE).
