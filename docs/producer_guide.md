# Producer Guide — building the normalized JSON (contract v1.0)

**Status**: 🔴 DA REVISIONARE · **Contract**: `format_version = "1.0"`
(JSON Schema: `src/genro_sqlmigration/schemas/structure-1.0.json`)

genro-sqlmigration is ORM-agnostic: it never reads your model. A
**producer** (your ORM extractor) projects the model into the
normalized JSON described here and injects it into the migrator:

```python
from genro_sqlmigration import SqlMigrator, StructureValidator

structure = my_extractor.get_json_struct()      # {'root': {...}}
migrator = SqlMigrator(db)                      # db: your Database facade
migrator.ormStructure = StructureValidator().validate(structure)
migrator.prepareMigrationCommands()
print(migrator.getChanges())                    # or migrator.applyChanges()
```

`validate()` normalizes missing containers, checks the structure
against the packaged JSON Schema (when the `validation` extra is
installed) plus the semantic rules a schema cannot express, and strips
the optional top-level `format_version` key.

A complete, tested reference producer lives in
`tests/support/orm_producer.py` (it ports the legacy Genropy
`orm_extractor` rules); the reference `Database`/`BaseAdapter`
implementation over psycopg 3 lives in `tests/support/pg_database.py`.

## 1. Shape

Build with the factories from `genro_sqlmigration`:

```python
from genro_sqlmigration import (
    new_structure_root, new_schema_item, new_table_item, new_column_item,
    new_constraint_item, new_relation_item, new_index_item,
)

structure = new_structure_root('mydb')          # {'root': {...}}
schemas = structure['root']['schemas']
schemas['alfa'] = new_schema_item('alfa')
table = new_table_item('alfa', 'alfa_recipe')
table['attributes']['pkeys'] = 'id'             # comma-joined physical columns
schemas['alfa']['tables']['alfa_recipe'] = table
table['columns']['id'] = new_column_item(
    'alfa', 'alfa_recipe', 'id', attributes={'dtype': 'serial', 'notnull': '_auto_'}
)
```

Hierarchy: `root` → `schemas` → `tables` → `columns` / `relations` /
`constraints` / `indexes`; `extensions` and `event_triggers` live at
root level. Every entity carries `entity`, `entity_name`,
`attributes`.

## 2. Attribute cleaning is normative (and lossy)

The factories strip attributes whose value is `None`, `False`, `{}`,
`[]`, `''` or `'NO ACTION'`. Consequence: the format cannot
distinguish "not specified" from "explicitly default" — **never emit
default-valued attributes**. Anything you set after a factory call
bypasses cleaning: set only meaningful values.

## 3. Column rules

Allowed attributes (`COL_JSON_KEYS`): `dtype`, `sql_type`, `notnull`,
`sqldefault`, `size`, `unique`, `extra_sql`, `generated_expression`,
`comment`. `sql_type` is the native-type escape hatch.

- **dtype codes** (closed set): `A B C D DH DHZ DT H HZ I L M N O P R
  T TSV VEC X Z jsonb serial`. Normalize ORM-internal types yourself:
  the legacy converter maps `X`/`Z`/`P` → `T`.
- **dtype defaults**: no dtype → `'A'` if a size is present, else `'T'`.
- **size normalization** (what the DB introspection will report back):
  - `':N'` → `'0:N'` and dtype `A` (varchar);
  - `'min:max'` → force min to 0 (`'0:max'`): the DB cannot know the min;
  - plain `'N'` with a text dtype (`A`/`T`/`X`/`Z`/`P` or none) → dtype
    `C` (char);
  - `'N'` with dtype `N` → size `'N,0'`; decimals stay `'p,s'`;
  - `A`/`C` without size → `T` (char without length is impossible).

## 4. Primary keys

- `table['attributes']['pkeys']` is the comma-joined list of
  **physical** column names (expand composite members yourself).
- Every pkey column gets `notnull='_auto_'`.
- Single-column PK: drop a redundant `unique=True` on that column.
- Composite PK: **keep** per-column `unique=True` (the PK tuple does
  not imply per-column uniqueness — legacy issue #580).
- Pkey columns get no separate index (the PK already creates one).

## 5. Foreign keys (relations)

Only **physical** relations project into the JSON; logical/navigable
relations are producer-side concepts.

- Key and `entity_name`: the structural hash
  `hashed_name(schema, table, columns, obj_type='fk')` (`fk_*`).
  Never change the hash function.
- Attributes: `columns`, `related_schema`, `related_table`,
  `related_columns`, `constraint_name` (= the hash), `constraint_type
  = "FOREIGN KEY"`, optional `on_delete`/`on_update` (never emit
  `NO ACTION`), `deferrable`/`initially_deferred` (only `True`).
- Legacy defaults worth replicating: `ON UPDATE CASCADE` on every FK;
  `SET NULL` delete actions imply deferrable + initially deferred.
- The FK **source** columns always get a supporting index (unless in
  the pkey or unique).
- FK to a **non-PK** target: also emit an index on the target
  column(s) of the related table (legacy oracle `test_06c`).

## 6. Indexes

- Key and `entity_name`: `hashed_name(..., obj_type='idx')` (`idx_*`).
- Attributes: `columns` as an ordered dict `{name: 'DESC'|None}`,
  optional `method`, `unique`, `with_options`, `tablespace`, `where`,
  `index_name` (= the hash).
- Columns with `unique=True` get no index (the constraint creates one).
- Per-dtype defaults: `TSV` columns always get a GIN index, even
  without `indexed=True`; an explicit method wins (legacy #629).

## 7. UNIQUE and CHECK constraints

- Multi-column UNIQUE: key/`entity_name` = `hashed_name(...,
  obj_type='cst')` (`cst_*`); attributes `columns`, `constraint_name`,
  `constraint_type="UNIQUE"`. Single-column unique is a **column
  attribute**, not a constraint entity.
- CHECK: key/`entity_name` = the **user-given** constraint name;
  attributes `constraint_name`, `constraint_type="CHECK"`,
  `check_clause`. Write the clause **as PostgreSQL prints it** (e.g.
  `(rating >= 0)`, with outer parentheses and explicit casts) so it
  compares clean against introspection — interim rule until the
  canonicalization probe lands (roadmap doc `05` §3).

## 8. Comments

- Column comment: the `comment` column attribute.
- Table comment: `table['attributes']['comment']`.
- Emitted as `COMMENT ON` idempotent replaces; removing the attribute
  clears the comment (`IS NULL`).

## 9. Extensions

Emit an extension item per required PostgreSQL extension:
`structure['root']['extensions'][name] = new_extension_item(name)`.
Generated SQL is `CREATE EXTENSION IF NOT EXISTS` — never dropped,
never recreated.

## 10. Re-injection rule

Command handlers mark transient state on the injected JSON (e.g.
`_rebuilt` on a column rebuilt via DROP+ADD). **Build and inject a
fresh structure before every** `prepareMigrationCommands()` — do not
reuse a structure that already went through a migration run.

## Riferimenti

- Contract notes and versioning policy: `roadmap/04_json_contract_notes.md`.
- Extended entities (views, functions, triggers, types, sequences):
  `roadmap/05_extended_entities.md`.
- JSON Schema: `src/genro_sqlmigration/schemas/structure-1.0.json`.
