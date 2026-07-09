"""Regression tests ported from the legacy dormant ``GeneralSqlMigrationCode``
suite (``gnrpy/tests/sql/test_gnrsqlmigration.py``, issue #534 fixes).

These run without a database and pin the backports listed in
``roadmap/02_legacy_parity_audit.md`` §A:

- bugs #1/#2: ``new_relation_item``/``new_index_item`` must not mutate the
  caller's attributes dict;
- bug #3: each multi-column UNIQUE constraint keeps its own name in
  ``DbExtractor.process_constraints`` (no stale loop variable);
- bug #4: ``changed_constraint`` reads ``constraint_name`` from the entity
  attributes, not from the commands nested defaultdict.

The legacy fifth test (``jsonModelWithoutMeta`` redundant re-extraction) is
not ported: that function was removed from the package (Bag/UI concerns stay
in Genropy — roadmap/02 §A.5).

Package-specific: ``ormStructure`` is injected by the caller, so
``SqlMigrator`` must expose it from construction and ``extractOrm()`` must
preserve the injected value (the legacy ``OrmExtractor`` wiring was removed).
"""

from unittest.mock import MagicMock

from genro_sqlmigration import SqlMigrator, new_index_item, new_relation_item
from genro_sqlmigration.command_builder import CommandBuilderMixin
from genro_sqlmigration.db_extractor import DbExtractor
from genro_sqlmigration.structures import nested_defaultdict


class TestFactoryMutation:
    """Bugs #1/#2: factories must copy the attributes dict, not mutate it."""

    def test_caller_dict_unchanged_after_new_relation_item(self):
        caller_attrs = {
            'related_table': 'other_table',
            'related_schema': 'public',
            'related_columns': 'id',
        }
        original_keys = set(caller_attrs.keys())

        new_relation_item(
            schema_name='public',
            table_name='my_table',
            columns=['fk_id'],
            attributes=caller_attrs,
        )

        assert set(caller_attrs.keys()) == original_keys, (
            "new_relation_item() should not add keys to the caller's dict; "
            f"found extra keys: {set(caller_attrs.keys()) - original_keys}"
        )

    def test_caller_dict_unchanged_after_new_index_item(self):
        caller_attrs = {
            'unique': True,
            'method': 'btree',
        }
        original_keys = set(caller_attrs.keys())

        new_index_item(
            schema_name='public',
            table_name='my_table',
            columns=['col_a', 'col_b'],
            attributes=caller_attrs,
        )

        assert set(caller_attrs.keys()) == original_keys, (
            "new_index_item() should not add keys to the caller's dict; "
            f"found extra keys: {set(caller_attrs.keys()) - original_keys}"
        )


class TestDbExtractorConstraints:
    """Bug #3: stale loop variable in multi-column UNIQUE processing."""

    def test_multi_unique_constraint_uses_own_constraint_name(self):
        """When two multi-column UNIQUE constraints exist, each should use
        its own constraint_name — not the last value of ``v`` from the
        previous loop."""
        extractor = DbExtractor.__new__(DbExtractor)

        extractor.json_schemas = {
            'myschema': {
                'tables': {
                    'mytable': {
                        'attributes': {'pkeys': 'id'},
                        'columns': {
                            'id': {'attributes': {}},
                            'a': {'attributes': {}},
                            'b': {'attributes': {}},
                            'c': {'attributes': {}},
                            'd': {'attributes': {}},
                        },
                        'constraints': {},
                        'indexes': {},
                        'relations': {},
                    }
                }
            }
        }

        constraints_dict = {
            ('myschema', 'mytable'): {
                'UNIQUE': {
                    'uq_ab': {
                        'columns': ['a', 'b'],
                        'constraint_name': 'uq_ab',
                    },
                    'uq_cd': {
                        'columns': ['c', 'd'],
                        'constraint_name': 'uq_cd',
                    },
                },
            }
        }

        extractor.process_constraints(constraints_dict, schemas=['myschema'])

        table_constraints = (
            extractor.json_schemas['myschema']['tables']['mytable']['constraints']
        )
        constraint_names = [
            c['attributes']['constraint_name']
            for c in table_constraints.values()
        ]
        assert 'uq_ab' in constraint_names, (
            f"Expected 'uq_ab' in constraint names, got {constraint_names}. "
            "Bug: stale loop variable 'v' overwrites constraint_name."
        )
        assert 'uq_cd' in constraint_names, (
            f"Expected 'uq_cd' in constraint names, got {constraint_names}."
        )


class TestChangedConstraint:
    """Bug #4: changed_constraint must read the name from entity attributes."""

    def test_changed_constraint_uses_entity_attributes(self):
        """changed_constraint() should read constraint_name from
        item['attributes'], not from the commands nested_defaultdict
        (which auto-creates empty entries on access)."""
        builder = CommandBuilderMixin.__new__(CommandBuilderMixin)

        mock_db = MagicMock()
        mock_db.adapter.struct_constraint_sql.return_value = (
            'CONSTRAINT uq_real UNIQUE("a", "b")'
        )
        builder.db = mock_db
        builder.ignore_constraint_name = False

        commands = nested_defaultdict()
        commands['db']['schemas']['myschema']['tables']['mytable'] = {
            'constraints': nested_defaultdict(),
        }
        builder.commands = commands

        item = {
            'entity_name': 'cst_hash_abc',
            'schema_name': 'myschema',
            'table_name': 'mytable',
            'attributes': {
                'constraint_name': 'uq_real',
                'constraint_type': 'UNIQUE',
                'columns': ['a', 'b'],
            },
        }

        builder.changed_constraint(
            item=item,
            entity_name='cst_hash_abc',
            changed_attribute='columns',
            oldvalue=['a'],
            newvalue=['a', 'b'],
        )

        call_kwargs = mock_db.adapter.struct_constraint_sql.call_args
        passed_name = call_kwargs.kwargs.get('constraint_name')
        assert passed_name == 'uq_real', (
            f"Expected constraint_name='uq_real' from entity attributes, "
            f"got '{passed_name}'. Bug: reads from commands dict instead."
        )


class TestOrmStructureInjection:
    """Package contract: ormStructure is injected by the caller.

    The legacy OrmExtractor wiring was removed from the package, so
    ``SqlMigrator`` must (a) expose an empty ``ormStructure`` from
    construction and (b) preserve an injected value across
    ``extractOrm()`` — which ``prepareStructures()`` always calls.
    """

    def test_orm_structure_empty_after_init(self):
        migrator = SqlMigrator(MagicMock())
        assert migrator.ormStructure == {}

    def test_extract_orm_preserves_injected_structure(self):
        migrator = SqlMigrator(MagicMock())
        injected = {'root': {'entity': 'db', 'entity_name': 'x', 'schemas': {}}}
        migrator.ormStructure = injected
        migrator.extractOrm()
        assert migrator.ormStructure is injected
