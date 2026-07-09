# Claude Code Instructions - Genro SQL Migration

**Parent Document**: [meta-genro-modules CLAUDE.md](https://github.com/softwellsrl/meta-genro-modules/blob/main/CLAUDE.md)

## Project-Specific Context

### Current Status

- Development Status: Alpha — Has Implementation: Yes (extracted from
  Genropy legacy `gnr.sql.gnrsqlmigration` on 2026-02-16; zero tests
  until milestone M1 lands)

### Project Goal

An **autonomous, well-documented Python library** for SQL schema
migrations: compare a normalized JSON description of a database (the
contract) against a live database and generate/apply the realignment
SQL. ORM-agnostic by design: the ORM extractor stays in the producer
(Genropy legacy today, the genro-sql builder tree tomorrow); this
package consumes only the JSON.

### Project-Specific Guidelines

- **Read `roadmap/00_INDEX.md` first** — charter, milestones, parity
  audit vs legacy, test-porting plan and JSON-contract notes live in
  `roadmap/`. A session handoff with full context is in `temp/`.
- **The JSON intermediate is the contract** (decided 2026-07-08; AST
  and XSD were evaluated and rejected). Do not change its shape
  casually; contract rules are in `roadmap/04_json_contract_notes.md`.
- **Never change `hashed_name`** (`structures.py`): the structural
  8-hex names (`cst_*`, `fk_*`, `idx_*`) are pinned by every legacy
  SQL oracle.
- **Dependency policy**: runtime deps stay minimal (`dictdiffer` +
  optional DB drivers as extras). No `gnr.*` imports, ever — that is
  the autonomy guarantee.
- **Legacy references** (read-only, for parity and oracles): Genropy
  worktree `/Users/gporcari/Sviluppo/Genropy/genropy/worktrees/develop`
  — `gnrpy/gnr/sql/gnrsqlmigration/` (modules),
  `gnrpy/tests/sql/test_gnrsqlmigration.py` (the exact-SQL oracle
  suite), `gnrpy/gnr/sql/adapters/` (origin of writers/readers).
- **DB targets**: PostgreSQL first (95% of real usage), then SQLite,
  MySQL, MSSQL — one reader/writer pair per dialect, capability-based
  feature gating.
- Related design context (wider rewrite this package serves):
  `sub-projects/genro-sql/roadmap/` docs 03 (§7 addendum), 06, 07.

---

**All general policies are inherited from the parent document.**
