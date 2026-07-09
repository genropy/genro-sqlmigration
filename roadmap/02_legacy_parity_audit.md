# M0 — Parity Audit: package vs legacy gnrsqlmigration

**Version**: 0.1.0 · **Last Updated**: 2026-07-08 · **Status**: 🔴 DA REVISIONARE

Package @ `9a91322` (extracted 2026-02-16) compared module-by-module
against Genropy `develop`, `gnrpy/gnr/sql/gnrsqlmigration/`. Six legacy
commits touch the tree after the extraction date: `3415335f5` (#528
split, 02-18), `99a292728` (#534 bug fixes, 02-18), `197f8f5df` (#580,
02-25), `88c14998b` (#629, 03-02), `f96f84e15` (#637 typing, 03-03),
`93bd657d4` (#655, 03-04). Diff sizes are small (9–96 changed lines
per module) and fully classified below.

## A. Legacy fixes MISSING in the package (regressions — backport in M1)

1. **Bug #3 (#534)** — `db_extractor.py`, multiple-unique constraints:
   package has pre-fix `constraint_name=v['constraint_name']` (stale
   loop variable); legacy uses
   `multiple_unique_const['constraint_name']`.
2. **Bug #4 (#534)** — `command_builder.py`, `changed_constraint`:
   package has pre-fix `constraints_dict['constraint_name']`; legacy
   uses `constraint_attr['constraint_name']`.
3. **Bugs #1/#2 (#534)** — `structures.py` factories
   `new_relation_item` / `new_index_item`: package lacks
   `attributes = dict(attributes or {})` and therefore mutates the
   caller's dict.
4. **#655 — connection-error taxonomy**: package `db_extractor`
   handles only `NonExistingDbException`; legacy distinguishes and
   re-raises the connection error. `exceptions.py` needs a
   connection-error class, raised (NOT the legacy `SystemExit`, which
   is wrong for a library).
5. Bug #5 (#534, `jsonModelWithoutMeta`) — N/A: function removed from
   the package (Bag/UI concerns stay in Genropy).

## B. Legacy fixes living in orm_extractor (outside the package) — producer-guide material

- **#580** — preserve `unique=True` on columns of composite primary
  keys (single-column PK still drops the redundant unique). A rule of
  JSON production → doc `04`.
- **#629** — auto-GIN index for TSV dtype: legacy added
  `DTYPE_INDEX_CONFIG = {'TSV': dict(method='gin', required=True)}` to
  `structures.py` plus orm_extractor usage. The package has neither.
  The producer decides indexes → doc `04`; decide whether
  `DTYPE_INDEX_CONFIG` re-enters package `structures.py` as shared
  reference data.

## C. Intentional package adaptations (verified, sound)

- Own exceptions (`SqlMigrationError`, `NonExistingDbException`)
  replacing `gnr.sql.gnrsql_exceptions`.
- No `_typing` mixin bases (#637 not applicable).
- No Bag: `json_to_tree`, `getDiffBag`, `jsonModelWithoutMeta` removed
  (admin-UI concerns stay in Genropy).
- No `OrmExtractor` wiring: `migrator.ormStructure` starts empty and
  is injected by the caller — the ORM-agnostic entry point.
- `gnr.dev.time_measure` decorator dropped.
- `# REVIEW` comments stripped (underlying behaviors unchanged).
- `struct_*_sql` → `writers/` (base + pg, 409 lines); `struct_get_*` →
  `readers/` (base + pg, 557 lines); `database.BaseAdapter` facade
  preserves the internal `self.db.adapter.struct_*` call surface, so
  `command_builder`/`executor`/`db_extractor` stay near-identical to
  legacy. Writer signatures cleaned (no `**kwargs`).

## D. Package-only divergences (track in the contract)

- `COL_JSON_KEYS` gains **`sql_type`** as 8th column attribute (legacy
  has 7; its orm_extractor never emits it) → document in the JSON
  Schema (doc `04`).
- `diff_engine.py`: `collection = dict(difflist)` +
  `collection.get('entity_name')` guard — addresses the legacy
  `dict(difflist)` fragility note. Improvement, keep.
- `GNR_DTYPE_CONVERTER` removed — Genropy-specific dtype normalization
  belongs to the producer → doc `04`.

## E. Addendum (2026-07-09) — regressions found by the M1 oracles, OUTSIDE the audit scope

The §A audit compared `gnrsqlmigration/` modules only. The M1 oracle
suite exposed further divergences in `writers/pg_writer.py` (derived
from `adapters/`, never audited) and one extraction leftover — all
fixed red→green with the tests that pin them:

1. **`create_schema_sql`** emitted `CREATE SCHEMA IF NOT EXISTS`;
   legacy (and oracles) emit `CREATE SCHEMA "x";`.
2. **Type map**: the extracted writer had a reduced `DTYPE_TO_SQL`
   with short names (`varchar`) and wrong entries (`R` → `smallint`;
   legacy `R` = `real`), missing A/C/N/DHZ/HZ/M/TSV/VEC/jsonb.
   Replaced with the full legacy `revTypesDict` (long names, which
   are also what introspection returns) as `REV_TYPES_DICT`, plus the
   legacy `columnSqlType` logic.
3. **`TYPE_CONVERSIONS`** was a reduced/divergent set (different
   USING expressions, no NULL guards, missing A/C sources, missing
   `R→I/L` ROUND). Replaced with the legacy base + PG-specific set.
4. **`create_extension_sql`** emitted `DROP EXTENSION IF EXISTS` +
   `CREATE EXTENSION`; legacy emits `CREATE EXTENSION IF NOT EXISTS`
   (pinned by the extension oracles: never drop, never recreate).
5. **`extractOrm()` missing**: `prepareStructures()` called it but the
   extraction had removed it with the OrmExtractor wiring — latent
   `AttributeError` for every consumer. Now `SqlMigrator` initializes
   `ormStructure = {}` and `extractOrm()` preserves the injected
   value (the ORM-agnostic injection contract, pinned by regression
   tests). Note: command handlers mark transient state on the
   injected JSON (e.g. `_rebuilt`), so producers must re-inject a
   fresh structure before re-running a diff — documented for the
   producer guide (doc `04`).

---

## Riferimenti

- Audit session: 2026-07-08 (module diffs verified line by line);
  addendum §E from the M1 porting session (2026-07-09).
- Legacy: genropy `develop`; package: `genro-sqlmigration` @ `9a91322`.
