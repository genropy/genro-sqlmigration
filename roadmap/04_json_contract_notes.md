# M2 — JSON Contract Notes (seed of the schema + producer guide)

**Version**: 0.1.0 · **Last Updated**: 2026-07-08 · **Status**: 🔴 DA REVISIONARE

The normalized JSON is **the contract** of this package (decision
closed 2026-07-08; AST and XSD evaluated and rejected — rationale in
doc `01` §3). Today the format lives implicitly in the `structures.py`
factories and the README; M2 makes it explicit, versioned and
validated. This document collects the normative rules and the known
limits that the JSON Schema and the producer guide must state.

## 1. Normative rules (already true, to be made explicit)

- **Shape**: every object is an entity dict with `entity`,
  `entity_name`, `attributes`; hierarchy navigable via name-keyed
  dicts (root → schemas → tables → columns / constraints / relations /
  indexes; plus db-level extensions / event_triggers).
- **Cleaned attributes are normative, and lossy**: None / False /
  empty values are stripped to avoid spurious diffs. Consequence: the
  format cannot distinguish "not specified" from "explicitly default".
  Producers MUST NOT emit default-valued attributes.
- **Hashed names are normative**: constraint/FK/index names are
  structural 8-hex hashes (`cst_*`, `fk_*`, `idx_*`) computed by
  `hashed_name` from schema+table+columns+kind. They make comparison
  deterministic and are pinned by every oracle. Changing the hash
  function invalidates the installed base — never change it.
- **Column attributes** (`COL_JSON_KEYS`): `dtype`, `sql_type`,
  `notnull`, `sqldefault`, `size`, `unique`, `generated_expression`,
  `extra_sql`. Note: `sql_type` is a package addition (the legacy
  contract had 7 keys and its orm_extractor never emits it) — the
  schema documents it as the native-type escape hatch.
- **dtype codes**: the Genro normalized set (see genro-sql doc `03`
  §5.1). Producer-side normalization of ORM-specific types (the
  legacy `GNR_DTYPE_CONVERTER`: X/Z/P → T) is the **producer's
  responsibility** — deliberately removed from the package.

## 2. Producer responsibilities (guide material — "how to get to the JSON")

Rules the legacy orm_extractor encodes and every producer must
replicate; discovered/confirmed in the M0 audit and to be exercised by
the M1 fixtures:

- **Unique on composite-PK members** (#580): a column that declares
  `unique=True` and belongs to a composite PK keeps its UNIQUE (the
  PK tuple does not imply per-column uniqueness); a single-column PK
  drops the redundant unique.
- **Per-dtype index defaults** (#629): TSV columns get a GIN index
  even without explicit `indexed=True`; explicit
  `indexed=dict(method=...)` wins. Decide whether
  `DTYPE_INDEX_CONFIG` re-enters `structures.py` as shared reference
  data or stays guide-only.
- **Composite PK members are NOT NULL** at DDL level (pinned by legacy
  oracle `test_05c`).
- **FK to non-PK column** implies a supporting index on the target
  (pinned by legacy oracle `test_06c`).
- Only **physical** relations project into the JSON (legacy: FK
  emitted only when the joiner has `foreignkey`); logical/navigable
  relations are producer-side concepts.

## 3. Known limits (accepted, documented)

- **No rename detection**: a state diff sees rename as drop+add (data
  loss). Mitigation planned post-M1: `previous_name` producer hint.
- **Lossy cleaning** (see §1) — by design.
- **Hash truncation**: 8-hex structural names; collision probability
  is non-zero but negligible at realistic schema sizes (legacy REVIEW
  note kept for the record).
- **CHECK constraints**: read from the DB but dropped by the extractor
  (legacy behavior); completed in wave 0 of the extended entities
  (doc `05` §2), which lands inside M2.

## 4. M2 deliverables

1. **JSON Schema** (draft 2020-12): rigorous — named `$defs` per
   entity, closed enums for `entity` and dtype codes, required keys,
   `additionalProperties` policy decided per entity; top-level
   `format_version` field.
2. **Semantic validation pass** in a `validate()` API at the package
   boundary (schema alone cannot express these): FK targets exist,
   pkey columns ⊆ columns, constraint columns exist, hashed names
   consistent with structure.
3. **Producer guide** (docs/): how to build the JSON from any ORM —
   factories usage, §2 rules, worked example (the M1 fixtures are the
   living example).
4. **Versioning policy**: what changes bump `format_version`, and the
   package's compatibility promise. The M2.5 entity waves (doc `05`
   §1) are the first planned minor bumps (1.1 views, 1.2
   functions/triggers, 1.3 types/sequences); `validate()` normalizes
   older-format input by inserting the containers a given
   `format_version` lacks.
5. **Wave 0 — CHECK constraints + comments** (design in doc `05` §2):
   `check_clause` on name-keyed CHECK constraint items; `comment` as
   9th `COL_JSON_KEYS` entry plus table-level comment attribute —
   included so schema v1.0 ships complete for all existing entity
   kinds.

---

## Riferimenti

- Contract decisions: doc `01` §3 (2026-07-08).
- Divergences vs legacy to encode: doc `02` §B/§D.
- Legacy dtype tables: genro-sql doc `03` §5.
