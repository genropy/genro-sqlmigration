# M2.5 — Extended Entities (views, functions, triggers, types, sequences)

**Version**: 0.1.0 · **Last Updated**: 2026-07-08 · **Status**: 🔴 DA REVISIONARE

Owner request (2026-07-08): the migration system must also define and
migrate views, procedures/functions, triggers and related entities
declared in the ORM. Verified state: **neither the package nor the
legacy engine migrates any of these today** — the boundary is
schemas/tables/columns/constraints/FK/indexes/extensions (event
triggers are introspected but `added_event_trigger` is a deliberate
no-op; legacy view support in `SqlModelChecker` is dead code). The
legacy `gnrsqlmigration/README.md` (lines 127–323) already planned the
JSON schemas for all of them plus the extension recipe: new factory in
`structures.py` + reader introspection + `added_/changed_/removed_`
handlers + `ENTITY_TREE` entry + writer DDL methods — `diff_engine.py`
and the executor slot model are generic. This document is the design.

ORM-side syntax for declaring these entities is the **producer's**
business (Genropy legacy / genro-sql); this package fixes the JSON
shape and the migration behavior.

## 1. Milestone placement and waves

M1 (oracle suite, unchanged, first) → M2 extended with **wave 0**
(CHECK + comments: they complete already-existing entity kinds, so
JSON Schema v1.0 ships complete) → **M2.5** in three waves, each a
minor `format_version` bump → M3 multi-DB (capability flags are born
with the entities that need them, before multi-DB).

| Wave | Entities | format_version |
|---|---|---|
| 0 (inside M2) | CHECK constraints, column/table comments | 1.0 |
| 1 | Views + materialized views | 1.1 |
| 2 | Functions/procedures, table triggers | 1.2 |
| 3 | Custom types (ENUM first), sequences | 1.3 |

Order driven by value (views most requested) and dependencies
(triggers require functions; types require the executor ordering
extension). Wave implementation starts ONLY with M1 green.

**Prerequisite refactor** (post-M1, pre-wave-1): consolidate the dual
extraction path — `DbExtractor` calls `db.adapter.struct_get_*` while
`PgReader` has its own `_process_*`; the introspection surface is not
declared on `BaseAdapter` (`database.py` declares only writer-side
`struct_*_sql` + `execute`/`connect`). Declare it once (on
`BaseReader`, adapters delegating) so each wave implements
introspection exactly once. Mechanical, fully protected by the M1
suite.

## 2. Contract shape per entity

All new entities follow `entity`/`entity_name`/`attributes`, based on
the legacy README schemas (lines 144–248):

- **Functions/procedures** (`schema_item.functions`): `language`,
  `return_type`, `arguments`, `body`, `volatility`, `security`,
  `is_procedure`.
- **Views/matviews** (`schema_item.views`): `definition`,
  `materialized`, `columns`, `with_data`.
- **Table triggers** (`table_item.triggers`): `timing`, `events`,
  `for_each`, `function_name`, `function_schema`, `condition`,
  `arguments`.
- **Custom types** (`schema_item.types`): `type_kind`
  (ENUM/COMPOSITE/DOMAIN/RANGE), `enum_values`, `columns`,
  `base_type`, `constraint`.
- **Sequences** (`schema_item.sequences`): `start_value`, `increment`,
  `min_value`, `max_value`, `cycle`, `owned_by`. Standalone only —
  serial/IDENTITY sequences stay implicit.
- **CHECK constraints** (wave 0): `new_constraint_item` gains a
  `check_clause` parameter and a CHECK branch; `pg_writer` already
  renders `CONSTRAINT ... CHECK (...)`. Missing pieces: extractor
  processing (today `db_extractor` drops them — "CHECK constraints not
  handled at this time"), `check_clause` passthrough in
  `added_constraint`/`changed_constraint`.
- **Comments** (wave 0): column comment as 9th `COL_JSON_KEYS` entry
  `"comment"` (`COMMENT ON COLUMN`); table comment in
  `table_item['attributes']['comment']` (`COMMENT ON TABLE`).

Rules:

- **Name-keyed, never hashed.** Views, functions, triggers, types,
  sequences carry user-given names: dict key = `entity_name` = the
  name. `hashed_name` is touched by nothing — only UNIQUE/FK/index
  names keep the 8-hex hashing. CHECK constraints are name-keyed too
  (the producer MUST name them): hashing on columns is meaningless
  for a clause and hashing the clause text would break on PG clause
  rewriting (§3).
- **Containers**: `schema_item` gains `views`, `functions`, `types`,
  `sequences`; `table_item` gains `triggers`; `ENTITY_TREE` extended
  accordingly (legacy README lines 301–323).
- **Version compatibility**: a producer emitting older-format JSON
  (missing containers) must not break the diff. `validate()` (M2)
  normalizes input — inserts missing empty containers keyed by
  `format_version`. This is the concrete compatibility story for the
  wave bumps.
- **Function keying** — open (§7): identity-signature key
  `name(argtypes)` (overload-correct; signature change naturally
  becomes removed+added, matching `CREATE OR REPLACE` limits) vs
  name-only with a documented "no overloads" v1 rule.
- **Attribute cleaning** applies unchanged (None/False/empty
  stripped): e.g. `materialized: false` never appears — schemas model
  these as optional-absent.

## 3. Diff semantics for body-carrying entities

PostgreSQL round-trips definitions through its parser
(`pg_get_viewdef`, `pg_get_constraintdef` re-print SQL: case,
whitespace, parens, casts, qualified names), so producer text ≠
introspected text. Naive comparison → permanent false diff → broken
idempotence. Per entity:

- **Functions: compare verbatim — no problem.** `pg_proc.prosrc`
  stores the submitted body byte-identical for plpgsql/classic-sql
  functions. JSON `body` carries the full text (needed for CREATE
  anyway); ordinary attribute comparison works; a whitespace edit
  triggers one `CREATE OR REPLACE`, after which prosrc equals the
  producer text again → idempotent. Producer-guide constraints: no
  PG14+ `BEGIN ATOMIC` bodies (those ARE re-normalized); spell types
  canonically (`integer`, not `int`) in `arguments`/`return_type`.
- **Views: canonicalize via DB round-trip, no stored state.** JSON
  `definition` carries the producer's SELECT verbatim. Before
  diffing, definitions of views that exist on both sides are
  canonicalized through the live DB: `CREATE TEMP VIEW __gsm_probe AS
  <definition>` + `pg_get_viewdef` (pg_temp, ~1ms, auto-dropped).
  The extractor stores `pg_get_viewdef` output. Canonical vs
  canonical is byte-equal iff semantically equal → zero false
  positives. Live-DB access during command preparation has precedent
  (`is_empty_column` in `command_builder`). Probe failure (definition
  references a relation added in the same run) → assume changed →
  `CREATE OR REPLACE VIEW`, converging at the next run. Rejected:
  hash marker in `COMMENT ON` (collides with wave-0 comments),
  package-owned metadata table (stateless-diff violation),
  Python-side SQL normalizer (unwinnable).
- **CHECK clauses: same canonicalizer, smaller scale** (temp table
  probe + `pg_get_constraintdef`). Wave 0 MAY ship with the
  documented producer rule "write the clause as PG prints it"; the
  probe (built in wave 1) then removes the rule.
- **Materialized views**: no `CREATE OR REPLACE` exists → definition
  change = DROP + `CREATE ... WITH [NO] DATA` per `with_data`.

## 4. Execution ordering

Current executor slots: `db_creation` (manager conn) →
`build_commands` (per schema: CREATE SCHEMA → per table:
pre_commands → CREATE/ALTER TABLE → constraints → indexes; all FKs
deferred to a global tail) → `extensions_commands`.

⚠️ **Discrepancy found (fix in wave 3 at the latest)**: the module
docstring of `executor.py` claims extensions run second (before
schemas), but `applyChanges()` executes `extensions_commands` LAST.
Types/functions may depend on extensions → extensions must actually
run before build_commands.

Minimal extension of the flat slot model:

```
per schema:  CREATE SCHEMA → types → sequences → functions → tables (as today)
global tail: FK relations (existing) → views (producer order) → matviews → triggers
```

- Types before tables (enum columns reference them); functions before
  tables (defaults/generated expressions) and before views.
- **Views may depend on views**: no SQL parsing. Contract: producers
  list views in dependency order (JSON dicts preserve insertion
  order; the extractor side needs no ordering). Optional
  `depends_on: [names]` attribute lets `validate()` verify/topo-sort
  when present. Executor stays dumb.
- Triggers last (depend on tables + functions).
- Removal ordering is the reverse (triggers → views → ...). All new
  `removed_*` handlers are **no-op by default**, matching the current
  safety stance (`removeDisabled` default).
- New command buckets: per-schema `types`/`sequences`/`functions`/
  `views`; per-table `triggers` and `post_commands` (parallel to
  `pre_commands`) for standalone `COMMENT ON` statements (column
  commands are joined into a single ALTER TABLE, comments cannot be).

## 5. ALTER vs drop+recreate (PostgreSQL rules driving the writers)

| Entity | ALTER-able | Drop+recreate | Notes |
|---|---|---|---|
| View | `CREATE OR REPLACE VIEW` only add-columns-at-end | incompatible column change (rename/retype/remove) | v1 default: always OR REPLACE, failure surfaces. Optional `rebuild_views` flag (§7) |
| Matview | nothing | any definition change; `materialized` flip | recreate `WITH [NO] DATA` |
| Function | body → `CREATE OR REPLACE`; volatility/security → `ALTER FUNCTION` | return/arg types, FUNCTION↔PROCEDURE | signature keying turns these into removed+added |
| Trigger | rename only | any other change → DROP+CREATE | `CREATE OR REPLACE TRIGGER` needs PG14+; use DROP+CREATE |
| ENUM | `ALTER TYPE ... ADD VALUE [BEFORE\|AFTER]` | value removal/reorder → raise `SqlMigrationError` | pin PG≥12 (pre-12: ADD VALUE not in transaction block; moot with per-statement autocommit) |
| DOMAIN | SET/DROP DEFAULT, SET/DROP NOT NULL, ADD/DROP CONSTRAINT | base type change | |
| COMPOSITE | `ALTER TYPE ADD/DROP/ALTER ATTRIBUTE` | — | error if in-use incompatible |
| Sequence | everything (`ALTER SEQUENCE`) | never | owned sequences filtered out via `pg_depend` |
| CHECK | nothing | change = DROP + ADD CONSTRAINT (existing pattern) | adding on violating data fails; `NOT VALID` deferred |
| Comments | always (`COMMENT ON ... IS ...` is an idempotent replace) | never | |

**Column↔type interplay (wave 3)**: an enum-typed column introspects
with its type name and falls through `PG_TYPES_MAP` to `'T'` → false
diff. Mechanism: the existing `sql_type` escape hatch in
`COL_JSON_KEYS` — extractor emits `sql_type=<type name>` for
user-defined types; producers of enum columns emit the same.

## 6. Capability flags and per-wave breakdown

Wave 1 introduces `CAPABILITIES` sets on `BaseReader`/`BaseWriter`
(e.g. `{'views', 'matviews'}`); dialects declare what they support,
the command builder skips (with a report warning) unsupported entity
kinds. This is the surface M3 dialects will fill.

Every wave touches: `structures.py` (factory + containers +
`ENTITY_TREE`), introspection (once, after the §1 refactor),
`command_builder.py` handlers, `writers/base_writer.py` +
`pg_writer.py`, `database.py` facade, `__init__.py` exports, JSON
Schema `$defs` + `validate()` + producer guide + oracle tests.
`diff_engine.py` needs no changes in any wave (generic
path/attribute walking, verified).

- **W0**: `new_constraint_item` CHECK branch; `COL_JSON_KEYS` +
  `"comment"`; CHECK query in constraint processing; `check_clause`
  passthrough in handlers; `comment_on_column_sql`/
  `comment_on_table_sql`; `comment` branch in `changed_column`/
  `changed_table`; executor `post_commands`.
- **W1**: `new_view_item`; view introspection (`pg_class` relkind
  v/m + `pg_get_viewdef` + `pg_matviews.ispopulated`); the
  **canonicalizer** (temp-probe round-trip, lives in the reader);
  `create_view_sql`/`drop_view_sql`; `CAPABILITIES` introduced;
  executor views/matviews tail slots.
- **W2**: `new_function_item`/`new_trigger_item`; `pg_proc`
  introspection (prosrc, identity args, result type, provolatile,
  prosecdef, prokind) and `pg_trigger` (`NOT tgisinternal`);
  `create_function_sql`/`alter_function_sql`/`create_trigger_sql`/
  `drop_trigger_sql`; executor functions/triggers slots.
- **W3**: `new_type_item`/`new_sequence_item`; `pg_type` (typtype
  e/c/d + `pg_enum` ordered by enumsortorder) and `pg_sequences`
  (owned filtered via `pg_depend`); enum diff in `changed_type`
  (inserted values with BEFORE/AFTER positions; removal/reorder →
  raise); `sql_type` emission for user-defined column types; fix the
  §4 extensions-ordering discrepancy.

## 7. Open questions (owner input needed — do not block M1)

1. **View change default**: conservative `CREATE OR REPLACE` only
   (fails loudly on incompatible column changes) vs opt-in
   `rebuild_views` flag (`DROP ... CASCADE` + recreate all declared
   views in producer order — safe only for model-managed views).
2. **Live-DB canonicalization probe**: acceptable as a diff-time
   dependency? It makes view/CHECK comparison impossible against a
   snapshot without a connection. Accept, or require a pure-JSON
   fallback mode (assume-changed)?
3. **Function keying**: `name(argtypes)` identity signature vs
   name-only + "no overloads" v1 contract rule.

## 8. Test plan per wave (M1-consistent: exact SQL → apply → idempotence)

Same three layers as doc `03` §4: writer unit tests (no DB), oracle
tests on ephemeral PostgreSQL with the `checkChanges` idempotence
assertion, regression pins.

- **W0**: CHECK inline in CREATE TABLE / added to existing table /
  clause changed (DROP+ADD) / violating data (error surfaces);
  column comment add/change/remove; table comment; quote escaping.
- **W1** (~25): view create on empty DB; added to existing DB;
  **messy-whitespace definition → apply → re-diff is zero** (the
  false-positive guard, single most important new test);
  OR-REPLACE-compatible change; view on a table added in the same
  run; two views in dependency order; matview WITH/WITH NO DATA;
  matview definition change; materialized flip.
- **W2** (~20): function create (plpgsql + sql, function +
  procedure); body change → OR REPLACE; volatility-only → ALTER;
  signature change → removed+added pair; body round-trip idempotence;
  trigger on a table created in the same run (ordering oracle);
  trigger timing/events/WHEN change → DROP+CREATE.
- **W3** (~15): enum create; enum column in the same run (ordering
  oracle); ADD VALUE at end and BEFORE; value removal raises; domain
  create/ALTER; sequence create; each ALTER SEQUENCE attribute;
  owned sequences excluded from extraction.

---

## Riferimenti

- Owner request and design session: 2026-07-08.
- Legacy planned schemas and extension recipe:
  `gnrpy/gnr/sql/gnrsqlmigration/README.md` lines 127–323 (genropy
  `develop`).
- Milestones and backlog: doc `01` §5–6; contract rules: doc `04`;
  test infrastructure: doc `03`.
