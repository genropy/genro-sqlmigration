# M1 — Test Porting Plan (the legacy oracle suite)

**Version**: 0.1.0 · **Last Updated**: 2026-07-08 · **Status**: 🔴 DA REVISIONARE

The package has **zero tests**. The legacy suite
`gnrpy/tests/sql/test_gnrsqlmigration.py` (1658 lines, 79 test
methods) is the de-facto spec of the migration engine: it pins the
**exact generated SQL** (whitespace-normalized string compare), then
applies it to a live PostgreSQL and asserts idempotence (re-running
the migrator yields zero residual changes). Porting it is the highest-
value single step for this package.

## 1. Source suite map

| Legacy base class | Tests | Collected as | Covers |
|---|---|---|---|
| `BaseGnrSqlMigration` | 50 | ×2 (postgres, postgres3) = 100 runs | create db/schema/table, columns, PK/composite PK, UNIQUE single/multi, FK (single/multi, to non-PK + auto index, onDelete/deferred), all dtype conversions (12a–12q, USING expressions) |
| `..._DefaultException` | 4 | postgres | incompatible conversion on non-empty column raises; empty column converts |
| `..._ForceMode` | 4 | postgres | `force=True`: unconvertible values → NULL (real data checked) |
| `..._BackupMode` | 8 | postgres | `backup=True`: `col__{dtype}` backup column, data preserved |
| `..._Extension` | 3 | ×2 = 6 runs | `CREATE EXTENSION IF NOT EXISTS`, no drop, no recreate |
| `ToDo` | 4 | **dormant** (no `Test` prefix) | FK/UNIQUE add-drop, change pkey — unimplemented features |
| `GeneralSqlMigrationCode` | 6 | **dormant** | MagicMock regressions for the five #534 bugs — no DB needed |

Key infrastructure: `checkChanges(expected)` (normalize → compare →
apply → assert idempotent), `normalize_sql` (whitespace collapse),
ephemeral PostgreSQL via `testing.postgresql`
(`gnrpy/tests/sql/common.py`), inline models built with
`cls.db.model.src.package(...).table(...).column(...)`.

## 2. The one real adaptation: fixtures build JSON, not ORM models

The legacy tests express fixtures through the Genropy ORM
(`db.model.src`) and let `orm_extractor` produce the JSON. The package
has no ORM by design. Ported tests therefore build the
**ormStructure JSON directly** — via the `structures.py` factories
(`new_schema_item`, `new_table_item`, `new_column_item`, …) plus plain
dicts — and inject it into the migrator. Consequences:

- The **SQL oracles stay byte-identical** (same expected strings,
  including hashed names `cst_*`/`fk_*`/`idx_*` — `hashed_name` must
  not change).
- The fixture-building code becomes the first real exercise of the
  producer API — direct input for the producer guide (doc `04`).
- Divergences found while translating fixtures (attributes the ORM
  set implicitly, e.g. #580 unique-on-composite-pkey, #629 TSV
  auto-GIN) are recorded in doc `04` as producer rules.

## 3. Porting order

1. **Regression six first** (`GeneralSqlMigrationCode`, dormant in
   legacy): activate as `Test*`, no DB needed — they pin exactly the
   §A backports of doc `02`. Red (pre-fix package) → backport → green.
2. **Connection-error tests** (from `test_connection_error.py`,
   migrator part): pin the new package connection-error exception
   (#655 backport; raised, not `SystemExit`).
3. **Base 50** on ephemeral PostgreSQL (psycopg3 only — the package
   has no psycopg2 extra; the legacy ran the same class on both
   drivers).
4. **Exception / Force / Backup / Extension** classes (19 runs).
5. **ToDo four**: keep dormant but ported (they document intended
   future features), renamed with an explicit `@pytest.mark.skip`
   and reason instead of the name-prefix trick.

## 4. Infrastructure choices

- `pytest` + ephemeral PostgreSQL (`testing.postgresql`, as legacy) in
  `tests/conftest.py`; markers to skip DB-bound tests when no PG is
  available (the regression six and pure-writer tests always run).
- Writer-level unit tests: the exact-SQL oracles double as direct unit
  tests of `pg_writer` methods (no DB, no migrator) — extra safety at
  minimal cost.
- Coverage config already in `pyproject.toml` (`--cov`); pre-push hook
  tolerates "no tests collected" until this plan lands, then the
  tolerance is removed.

## 5. Outcome (2026-07-09) — port completed

Suite landed: **81 passed, 5 skipped** (the dormant intended-features,
now explicit `skip` markers in `tests/test_todo_features.py`).
Corrections to the plan discovered while porting:

- Dormant counts were 5+5 (not 6+4): `GeneralSqlMigrationCode` has 5
  methods, of which the `jsonModelWithoutMeta` one is N/A (function
  removed from the package); `ToDo` has 5 (incl. `test_10b`).
- Layout: `tests/support/` holds the infrastructure — `pg_database.py`
  (concrete `Database`/`BaseAdapter` over psycopg3 + `PgWriter` +
  legacy-format `struct_get_*` introspection), `orm_producer.py`
  (fixture model + faithful port of the legacy `orm_extractor`
  projection rules — producer-guide material), `migration_base.py`
  (`checkChanges` with exact compare → apply → idempotence re-diff).
- The idempotence re-diff rebuilds the ormStructure first (the legacy
  OrmExtractor re-extracted on every prepare; handlers mark transient
  `_rebuilt` state on the injected JSON) — see doc `02` §E.5.
- Fixes landed with the suite: doc `02` §A backports (#534 ×4, #655)
  plus the §E writer/extractOrm regressions found by the oracles.
- `ruff check src/ tests/` clean; pre-push hook tolerance for
  "no tests collected" removed.

---

## Riferimenti

- Legacy suite: `gnrpy/tests/sql/test_gnrsqlmigration.py`,
  `test_connection_error.py`, `common.py` (genropy `develop`).
- Audit of what the tests must pin: doc `02` §A (+ §E addendum).
