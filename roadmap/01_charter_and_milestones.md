# Charter and Milestones

**Version**: 0.1.1 · **Last Updated**: 2026-07-09 · **Status**: 🔴 DA REVISIONARE

## 1. Goal

An **autonomous, well-documented Python library** for SQL schema
migrations. It compares two instances of a normalized JSON structure —
one produced from an application model (any ORM), one introspected
from a live database — and generates, orders and optionally applies
the SQL needed to realign the database.

Scope fixed by the owner (2026-07-08):

1. **ORM-agnostic, guaranteed** — the package never imports an ORM;
   producers build the JSON. The Genropy `orm_extractor` stays in
   Genropy legacy; the future genro-sql builder tree will project into
   the same JSON.
2. **Migrations toward multiple databases** — PostgreSQL first (95% of
   real usage), then SQLite, MySQL, MSSQL; one reader/writer pair per
   dialect with capability-based gating.
3. **A documented, validated JSON contract** — including guidance for
   producers on how to build the JSON ("how to get there"), and
   validation at the package boundary.

## 2. Architecture recap (as extracted, verified 2026-07-08)

- **Core** (shared, dialect-free): `structures.py` (entity factories,
  `hashed_name`, cleaning), `diff_engine.py` (dictdiffer → semantic
  events), `command_builder.py` (events → SQL fragments),
  `executor.py` (ordering + execution + backup verify), `migrator.py`
  (orchestrator; `ormStructure` injected by the caller),
  `db_extractor.py` (DB → JSON via reader), `exceptions.py`.
- **Per-dialect**: `readers/` (introspection → JSON;
  `pg_reader.py` ports the legacy `struct_get_*` queries) and
  `writers/` (DDL fragments; `pg_writer.py` ports the legacy
  `struct_*_sql` surface).
- **`database.py`**: `Database`/`BaseAdapter` facade replicating the
  legacy `db`/`db.adapter` interface consumed by the core modules —
  this is why `command_builder`/`executor`/`db_extractor` are
  near-identical to legacy (9–23 changed lines each, adaptations only).
- Dependencies: `dictdiffer` (runtime), `psycopg[binary]>=3.1` as
  `postgresql` extra. No `gnr.*` imports anywhere.

## 3. Contract decisions (closed 2026-07-08 — do not reopen)

- **The intermediate stays JSON.** Chosen originally for speed; its
  decisive value is decoupling: serializable snapshots, literal test
  fixtures, language-agnostic producers, external validation.
- **AST rejected as contract**: rich typed trees already exist at both
  ends (legacy model obj, genro-sql source tree); the intermediate
  must stay the dumb, stable common denominator. Typed dataclasses MAY
  appear later as an internal view that serializes to the same JSON
  (hardening, post-M1).
- **XSD/XML rejected as contract**: the engine is dict-native end to
  end; XML would add an object-model layer and text-encoding
  conventions for no decisive gain. XSD's genuine plus (`keyref`) is
  recovered by a semantic validation pass inside `validate()`. The
  XSD affinity of genro-builders belongs to the *model source*
  round-trip in genro-sql, not to this contract.

## 4. Consumers

- **Genropy legacy** — keeps `orm_extractor`, produces the JSON, will
  depend on this package (integration after M1 makes it safe).
- **genro-sql** (future) — projects the builder source tree into the
  JSON; full DDL render may be expressed as "diff against an empty
  database" (to be confirmed on the genro-sql side).

## 5. Milestones

- **M0 — Parity audit** ✅ done 2026-07-08 → doc `02`. Outcome: 4
  regressions to backport, 2 producer-side rules to document, the
  adaptations verified sound.
- **M1 — Test suite**: port the legacy oracle suite (122 exact-SQL
  cases on live PostgreSQL + activate the 10 dormant tests) → doc
  `03`. The §A backports of doc `02` land WITH the tests that pin
  them (red from legacy → fix → green). Package goes from 0 tests to
  the full de-facto spec.
- **M2 — JSON contract**: rigorous JSON Schema (`$defs`, closed
  enums, `format_version`), semantic validation pass (referential
  coherence) and producer guide → doc `04`. Includes **wave 0** of
  the extended entities (CHECK constraints + comments — they complete
  existing entity kinds, so schema v1.0 ships complete) → doc `05`.
- **M2.5 — Extended entities** (owner request 2026-07-08): views +
  materialized views (1.1), functions/procedures + table triggers
  (1.2), custom types + sequences (1.3) — three waves, each a minor
  `format_version` bump; capability flags on readers/writers born
  here. Full design → doc `05`. Starts only with M1 green.
- **M3 — Multi-DB**: `sqlite` reader/writer (reduced capabilities),
  then `mysql`, `mssql`. One dialect at a time, each with its oracle
  subset.
- **M4 — Consumers integration**: Genropy legacy depends on the
  package; genro-sql tree→JSON projection when its grammar exists.

## 6. Hardening backlog (post-M1, decide then)

- Typed diff events (dataclasses) replacing dict fragments — the
  fragile generic→semantic adaptation layer.
- Native structural walker (name-keyed JSONs) replacing `dictdiffer`
  (frozen upstream since 2021) — only with the suite green.
- `previous_name` producer hint for rename support (state diffs cannot
  detect renames; today rename = drop+add).
- Migration atomicity option (legacy applies with per-statement
  autocommit; a failed run leaves the DB partially migrated).
- Opt-in `rebuild_views` flag for incompatible view changes (wave 1
  ships conservative `CREATE OR REPLACE` only — doc `05` §7.1,
  decided 2026-07-09). If a real case demands it, prefer a targeted
  drop of the declared views in reverse producer order over
  `DROP ... CASCADE`.
- ~~Missing entities inherited from the legacy roadmap~~ — promoted
  to milestone M2.5 (owner request 2026-07-08), design in doc `05`.

---

## Riferimenti

- Owner decisions: session of 2026-07-08.
- Legacy: genropy `develop`, `gnrpy/gnr/sql/gnrsqlmigration/`.
- Wider rewrite: `sub-projects/genro-sql/roadmap/`.
