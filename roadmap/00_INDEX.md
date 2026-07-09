# genro-sqlmigration — Design Documentation Set

**Version**: 0.1.0 · **Last Updated**: 2026-07-08 · **Status**: 🔴 DA REVISIONARE

Documentation set for the evolution of genro-sqlmigration into an
autonomous, well-documented migration library. Documents keep the 🔴
status until reviewed and approved (🟡 / 🟢).

| Doc | Content |
|---|---|
| [01_charter_and_milestones.md](01_charter_and_milestones.md) | Goal, fixed scope, architecture recap, closed contract decisions (JSON vs AST/XSD), consumers, milestones M1–M4, hardening backlog |
| [02_legacy_parity_audit.md](02_legacy_parity_audit.md) | M0 audit vs legacy `gnr.sql.gnrsqlmigration`: 4 regressions to backport, producer-side fixes, intentional adaptations, package-only divergences |
| [03_test_porting_plan.md](03_test_porting_plan.md) | M1 plan: the legacy oracle suite (122 exact-SQL cases + 10 dormant), how fixtures translate from ORM calls to JSON construction, porting order |
| [04_json_contract_notes.md](04_json_contract_notes.md) | M2 seed: normative rules of the JSON contract, producer responsibilities, known limits, JSON Schema plan |
| [05_extended_entities.md](05_extended_entities.md) | M2.5 design: views, functions/procedures, table triggers, custom types, sequences, CHECK, comments — contract shapes, diff semantics, ordering, ALTER matrix, wave plan |

Context documents elsewhere:

- Session handoff (Italian, restart prompt): `temp/handoff_2026-07-08.md`
- Wider rewrite context: `sub-projects/genro-sql/roadmap/` — doc `03`
  (migration inventory + §7 addendum), doc `06` (adapter inventory —
  origin of writers/readers), doc `07` (2026 compiler experiments).
- Legacy source of truth: Genropy worktree
  `/Users/gporcari/Sviluppo/Genropy/genropy/worktrees/develop`
  (`gnrpy/gnr/sql/gnrsqlmigration/`, `gnrpy/tests/sql/`), plus
  genropy `develop` for post-extraction commits.
