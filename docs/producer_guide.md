# Producer Guide — describing your database

**Contract**: `format_version = "1.0"`

genro-sqlmigration is ORM-agnostic: it never reads your model. You
describe the desired database in one of three input formats, the
library compares it against the live database and generates (or
applies) the realignment SQL.

The three doors, from the friendliest to the most technical:

1. **Human JSON** — a readable JSON document with names and lists
   (no hashes). Compiled by `JsonStructureProducer`.
2. **XML** — the same information in XML, validated by the packaged
   XSD (`sql_model-1.0.xsd`); ideal for editors and GUIs. Compiled by
   `XmlStructureProducer`.
3. **Normalized JSON** — the internal contract itself, built with the
   `new_*_item` factories. For producers that need full control
   (e.g. an ORM extractor).

The two external formats are equivalent: the same model expressed in
human JSON or XML compiles to the identical internal structure. Both
compilers are thin front-ends over the same factories, so every
normalization rule below applies to both.

## 1. End-to-end quickstart (human JSON)

```python
from genro_sqlmigration import (
    JsonStructureProducer, PgDatabase, SqlMigrator, StructureValidator,
)

model = {
    "db": "mydb",
    "schemas": [
        {
            "name": "public",
            "tables": [
                {
                    "name": "author", "pkey": "id",
                    "columns": [
                        {"name": "id", "dtype": "serial"},
                        {"name": "name", "dtype": "A", "size": "0:120",
                         "notnull": True},
                    ],
                },
                {
                    "name": "recipe", "pkey": "id",
                    "columns": [
                        {"name": "id", "dtype": "serial"},
                        {"name": "title", "dtype": "A", "size": "0:80",
                         "notnull": True},
                        {"name": "author_id", "dtype": "L"},
                    ],
                    "relations": [
                        {"columns": ["author_id"],
                         "related_schema": "public",
                         "related_table": "author",
                         "related_columns": ["id"],
                         "on_delete": "CASCADE"},
                    ],
                    "indexes": [{"columns": ["title"]}],
                },
            ],
        },
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

`JsonStructureProducer` also accepts a JSON string or a file
(`JsonStructureProducer.from_file(path)`). Swap `PgDatabase` for
`SqliteDatabase`, `MysqlDatabase` or `MssqlDatabase` for the other
dialects (each needs its driver extra — see the README).

`StructureValidator().validate()` checks the compiled structure against
the packaged JSON Schema (with the `validation` extra installed) plus
the semantic rules a schema cannot express.

## 2. The human JSON format

Top level:

```text
{
  "db": "<database name>",
  "schemas":        [ {"name": "...", "tables": [ ... ]} ],
  "extensions":     [ "unaccent" ],
  "event_triggers": [ {"name": "audit_ddl",
                       "attributes": {"event": "ddl_command_end"}} ]
}
```

`extensions` and `event_triggers` are optional (PostgreSQL-oriented).

### Table

```text
{
  "name": "recipe",
  "pkey": "id",                  // comma-joined for composite: "a,b"
  "comment": "Recipes",
  "columns":     [ ... ],
  "relations":   [ ... ],        // foreign keys
  "constraints": [ ... ],        // UNIQUE / CHECK
  "indexes":     [ ... ]
}
```

Primary-key handling is automatic: every `pkey` column becomes
`notnull` (`'_auto_'`), and a single-column PK drops a redundant
`unique` on that column.

### Column

```json
{"name": "title", "dtype": "A", "size": "0:80", "notnull": true}
```

Accepted attributes: `dtype`, `size`, `notnull`, `sqldefault`,
`unique`, `sql_type`, `extra_sql`, `generated_expression`, `comment`.
Anything else is ignored. `sql_type` is the native-type escape hatch
(emitted verbatim in the DDL).

If `dtype` is missing: `'A'` when a `size` is present, `'T'`
otherwise.

### Relation (foreign key)

```json
{"columns": ["author_id"],
 "related_schema": "public", "related_table": "author",
 "related_columns": ["id"],
 "on_delete": "CASCADE",              // optional; never emit NO ACTION
 "name": "fk_recipe_author"}          // optional readable name
```

`columns` and `related_columns` are lists — a multi-column FK is just
a longer list (positional pairing). Optional flags: `on_update`,
`deferrable`, `initially_deferred` (emit only when true).

### Constraint

```json
{"type": "UNIQUE", "columns": ["title", "author_id"], "name": "uq_t_a"}
{"type": "CHECK", "name": "ck_recipe_title",
 "check_clause": "(char_length(title) > 0)"}
```

`name` is optional for UNIQUE, **required** for CHECK. Write the CHECK
clause as PostgreSQL prints it (outer parentheses, explicit casts) so
it compares clean against introspection. Single-column uniqueness is
the column attribute `unique`, not a constraint.

### Index

```json
{"columns": ["title"]}                              // plain list
{"columns": {"pa": null, "created": "DESC"},        // per-column sort
 "unique": true, "method": "btree",
 "with_options": {"fillfactor": "70"},
 "where": "created > '2020-01-01'",
 "name": "idx_recent"}                              // optional name
```

`columns` is either a list (all default sort) or an ordered map
`name → null | "DESC"`. `with_options` is passed through to the
dialect writer.

### Optional names

An explicit `name` on a relation / UNIQUE / index becomes the entity's
readable SQL name. Internally the entity is always keyed by a
structural hash computed from schema + table + columns — two models
that differ only in names produce the same keys, so renaming never
causes spurious diffs (`SqlMigrator` ignores name differences by
default, `ignore_constraint_name=True`).

## 3. The XML format

The same model, XSD-validated (`src/genro_sqlmigration/schemas/
sql_model-1.0.xsd`, namespace `urn:genro:sql-model:1.0`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<db xmlns="urn:genro:sql-model:1.0" name="mydb">
  <schema name="public">
    <table name="author" pkey="id">
      <column name="id" dtype="serial"/>
      <column name="name" dtype="A" size="0:120" notnull="true"/>
    </table>
    <table name="recipe" pkey="id">
      <column name="id" dtype="serial"/>
      <column name="title" dtype="A" size="0:80" notnull="true"/>
      <column name="author_id" dtype="L">
        <relation to="public.author.id" on_delete="CASCADE"/>
      </column>
      <!-- multi-column FK: table-level, ordered <to> children -->
      <relation columns="pa,pb" name="fk_pair">
        <to schema="s" table="parent" column="a"/>
        <to schema="s" table="parent" column="b"/>
      </relation>
      <constraint type="UNIQUE" columns="title,author_id"/>
      <constraint type="CHECK" name="ck_recipe_title"
                  check_clause="(char_length(title) &gt; 0)"/>
      <index columns="title"/>
      <index name="idx_recent" unique="true">
        <column name="pa"/>
        <column name="created" sort="DESC"/>
        <option key="fillfactor" value="70"/>
      </index>
    </table>
  </schema>
  <extension name="unaccent"/>
  <event_trigger name="audit_ddl">
    <option key="event" value="ddl_command_end"/>
  </event_trigger>
</db>
```

XML-specific notes:

- a single-column FK nests `<relation to="schema.table.column">` inside
  its column; a multi-column FK is a table-level `<relation>` with a
  `columns` attribute and ordered `<to>` children;
- `indexed="true"` on a column is a shortcut that generates a
  single-column index;
- `<option key value>` children carry free key/value pairs (index WITH
  options, event-trigger attributes);
- compile with `XmlStructureProducer` and the flow is identical to the
  quickstart:

```python
from genro_sqlmigration import XmlStructureProducer

structure = XmlStructureProducer(xml_text).get_json_struct()
# or XmlStructureProducer.from_file(path)
```

The inverse, `struct_to_xml(structure)`, de-normalizes an internal
structure (e.g. introspected from a live DB) back into editable XML.

## 4. dtype reference

Closed set of normalized type codes (PostgreSQL rendering shown; each
dialect writer maps them to its native equivalents):

| dtype | SQL type (PostgreSQL) | dtype | SQL type (PostgreSQL) |
| --- | --- | --- | --- |
| `A` | character varying(size) | `L` | bigint |
| `B` | boolean | `M` | money |
| `C` | character(size) | `N` | numeric(p,s) |
| `D` | date | `O` | bytea |
| `DH` | timestamp without time zone | `R` | real |
| `DHZ` | timestamp with time zone | `T` | text |
| `DT` | interval | `TSV` | tsvector |
| `H` | time without time zone | `VEC` | vector |
| `HZ` | time with time zone | `X`, `Z`, `P` | text |
| `I` | integer | `jsonb` | jsonb |
| `serial` | serial8 | | |

Size conventions: `'0:80'` = varchar(80); `'10'` with a char dtype =
char(10); `'12,2'` = numeric(12,2).

## 5. What NOT to emit (attribute cleaning)

The compilers strip attributes whose value is `None`, `False`, `{}`,
`[]`, `''` or `'NO ACTION'`. The format cannot distinguish "not
specified" from "explicitly default", so **never emit default-valued
attributes**: no `notnull: false`, no `on_delete: "NO ACTION"`, no
empty strings. Emit an attribute only when it carries a non-default
value.

## 6. The normalized contract (advanced)

The compiled form — what `get_json_struct()` returns and
`StructureValidator` checks (JSON Schema:
`src/genro_sqlmigration/schemas/structure-1.0.json`):

- Hierarchy: `root` → `schemas` → `tables` → `columns` / `relations` /
  `constraints` / `indexes`; `extensions` and `event_triggers` at root.
  Every entity carries `entity`, `entity_name`, `attributes`.
- FK / UNIQUE / index dict keys and `entity_name` are the structural
  hashes `fk_*` / `cst_*` / `idx_*` (`hashed_name(schema, table,
  columns, obj_type)`). CHECK constraints are keyed by their required
  user-given name.
- `table['attributes']['pkeys']` is the comma-joined physical column
  list; pkey columns carry `notnull='_auto_'`.
- Index `columns` is an ordered map `{name: 'DESC'|None}`; the hash is
  computed on the column names only.
- An explicit readable name lives in `constraint_name` / `index_name`;
  the key stays the hash.

Producers that need full control build this form directly with the
factories exported by the package (`new_structure_root`,
`new_schema_item`, `new_table_item`, `new_column_item`,
`new_relation_item`, `new_constraint_item`, `new_index_item`,
`new_extension_item`, `new_event_trigger_item`) — the compilers
themselves are ~150-line reference producers. A complete ORM-side
producer lives in `tests/support/orm_producer.py`.

## 7. Re-injection rule

Command handlers mark transient state on the injected structure.
**Build and inject a fresh structure before every**
`prepareMigrationCommands()` — never reuse a structure that already
went through a migration run.

## References

- JSON Schema (normalized contract):
  `src/genro_sqlmigration/schemas/structure-1.0.json`
- XSD (external XML format):
  `src/genro_sqlmigration/schemas/sql_model-1.0.xsd`
- Reference producers: `src/genro_sqlmigration/json_producer.py`,
  `src/genro_sqlmigration/xml_producer.py`,
  `tests/support/orm_producer.py`
