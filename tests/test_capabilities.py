"""Tests for the dialect capability gating (M3 phase 0).

Representation capabilities are stripped from the ORM structure before
the diff (an attribute the dialect cannot store never appears on the DB
side, so keeping it would produce a permanent false diff); DDL-operation
capabilities gate command generation. PostgreSQL declares the full set,
so the mechanism is a no-op for the whole oracle suite.
"""

import copy

from genro_sqlmigration import PgDatabase, SqlMigrator
from genro_sqlmigration.structures import (
    nested_defaultdict,
    new_column_item,
    new_constraint_item,
    new_extension_item,
    new_index_item,
    new_relation_item,
    new_schema_item,
    new_structure_root,
    new_table_item,
)
from genro_sqlmigration.writers import PgWriter


def _migrator(capabilities):
    """Real migrator on a PgDatabase whose writer declares ``capabilities``."""
    db = PgDatabase({'dbname': 'capdb'}, application_schemas=['alfa'])
    db.adapter.writer.CAPABILITIES = capabilities
    return SqlMigrator(db)


def _structure():
    """ORM structure exercising every representation capability."""
    structure = new_structure_root('capdb')
    root = structure['root']
    root['extensions']['uuid-ossp'] = new_extension_item('uuid-ossp')
    root['schemas']['alfa'] = new_schema_item('alfa')
    table = new_table_item('alfa', 'doc')
    table['attributes']['comment'] = 'a table comment'
    table['columns']['title'] = new_column_item(
        'alfa', 'doc', 'title',
        attributes={'dtype': 'T', 'comment': 'a column comment'},
    )
    relation = new_relation_item(
        'alfa', 'doc', ['owner_id'],
        attributes={
            'columns': ['owner_id'], 'related_schema': 'alfa',
            'related_table': 'users', 'related_columns': ['id'],
            'deferrable': True, 'initially_deferred': True,
        },
    )
    table['relations'][relation['entity_name']] = relation
    constraint = new_constraint_item(
        'alfa', 'doc', ['title', 'owner_id'], constraint_type='UNIQUE',
    )
    table['constraints'][constraint['entity_name']] = constraint
    index = new_index_item(
        'alfa', 'doc', ['title'],
        attributes={
            'columns': {'title': None}, 'method': 'gin',
            'tablespace': 'fastspace', 'where': 'title IS NOT NULL',
            'with_options': {'fillfactor': '70'},
        },
    )
    table['indexes'][index['entity_name']] = index
    root['schemas']['alfa']['tables']['doc'] = table
    return structure


class TestApplyCapabilities:
    def test_full_set_is_noop(self):
        migrator = _migrator(PgWriter.CAPABILITIES)
        migrator.ormStructure = _structure()
        untouched = copy.deepcopy(migrator.ormStructure)
        migrator.applyCapabilities()
        assert migrator.ormStructure == untouched
        assert migrator.warnings == []

    def test_empty_structure_is_noop(self):
        migrator = _migrator(frozenset())
        migrator.ormStructure = {}
        migrator.applyCapabilities()
        assert migrator.warnings == []

    def test_entity_kinds_stripped(self):
        migrator = _migrator(frozenset())
        migrator.ormStructure = _structure()
        migrator.applyCapabilities()
        root = migrator.ormStructure['root']
        assert root['extensions'] == {}
        assert any(
            "unsupported 'extensions'" in w and 'uuid-ossp' in w
            for w in migrator.warnings
        )

    def test_containers_stripped(self):
        migrator = _migrator(frozenset())
        migrator.ormStructure = _structure()
        migrator.applyCapabilities()
        table = migrator.ormStructure['root']['schemas']['alfa']['tables']['doc']
        assert table['relations'] == {}
        assert table['constraints'] == {}
        for capability in ('foreign_keys', 'table_constraints'):
            assert any(
                f"unsupported '{capability}'" in w for w in migrator.warnings
            )

    def test_attributes_stripped_with_warnings(self):
        # Keep the entity containers so the attribute strips are observable
        migrator = _migrator(frozenset({'foreign_keys', 'table_constraints'}))
        migrator.ormStructure = _structure()
        migrator.applyCapabilities()
        table = migrator.ormStructure['root']['schemas']['alfa']['tables']['doc']
        assert 'comment' not in table['attributes']
        assert 'comment' not in table['columns']['title']['attributes']
        relation = next(iter(table['relations'].values()))
        assert 'deferrable' not in relation['attributes']
        assert 'initially_deferred' not in relation['attributes']
        index = next(iter(table['indexes'].values()))
        for attribute in ('method', 'tablespace', 'where', 'with_options'):
            assert attribute not in index['attributes']
        for capability in ('comments', 'fk_deferrable', 'index_where',
                           'index_method', 'index_tablespace',
                           'index_with_options'):
            assert any(
                f"unsupported '{capability}'" in w for w in migrator.warnings
            )


class TestUnsupportedHelper:
    def test_missing_capability_warns_and_returns_true(self):
        migrator = _migrator(frozenset())
        assert migrator.unsupported('alter_column_type', 'a context') is True
        assert migrator.warnings == [
            "unsupported 'alter_column_type': skipped a context"
        ]

    def test_present_capability_is_silent(self):
        migrator = _migrator(PgWriter.CAPABILITIES)
        assert migrator.unsupported('alter_column_type', 'a context') is False
        assert migrator.warnings == []


class TestCommandGates:
    def _column_item(self):
        return {
            'schema_name': 'alfa', 'table_name': 'doc',
            'entity_name': 'title', 'attributes': {'dtype': 'L'},
        }

    def test_dtype_change_skipped_without_alter_column_type(self):
        migrator = _migrator(frozenset())
        migrator.commands = nested_defaultdict()
        migrator.changed_column(
            item=self._column_item(), changed_attribute='dtype',
            oldvalue='I', newvalue='L',
        )
        assert len(migrator.commands) == 0
        assert any(
            "unsupported 'alter_column_type'" in w for w in migrator.warnings
        )

    def test_dtype_change_generated_with_full_capabilities(self):
        migrator = _migrator(PgWriter.CAPABILITIES)
        migrator.commands = nested_defaultdict()
        migrator.changed_column(
            item=self._column_item(), changed_attribute='dtype',
            oldvalue='I', newvalue='L',
        )
        assert len(migrator.commands) > 0
        assert migrator.warnings == []
