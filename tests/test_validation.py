"""Contract validation tests (M2): StructureValidator + format_version.

No database needed. Fixtures are built with the test producer
(``tests/support/orm_producer.py``) so the happy path exercises exactly
what a real producer emits, including wave-0 entities (CHECK constraints
and comments).
"""

import pytest

from genro_sqlmigration import FORMAT_VERSION, SqlMigrator, StructureValidator
from genro_sqlmigration.exceptions import SqlValidationError

from .support.orm_producer import OrmJsonProducer, SrcModel


def sample_structure():
    """A representative producer output: FK, unique, index, CHECK, comments."""
    model = SrcModel()
    pkg = model.package('alfa', sqlschema='alfa')
    author = pkg.table('author', pkey='id', comment='Authors registry')
    author.column('id', dtype='serial')
    author.column('name', size=':45', unique=True, comment='Full name')
    recipe = pkg.table('recipe', pkey='id')
    recipe.column('id', dtype='serial')
    recipe.column('title', indexed=True)
    recipe.column('author_id', dtype='L').relation('alfa.author.id',
                                                   mode='foreignkey')
    recipe.column('rating', dtype='I')
    recipe.checkConstraint('chk_recipe_rating', '(rating >= 0)')
    return OrmJsonProducer(model, 'validation_db').get_json_struct()


class TestStructureValidator:

    def test_producer_output_validates(self):
        normalized = StructureValidator().validate(sample_structure())
        assert 'root' in normalized
        assert 'format_version' not in normalized

    def test_format_version_accepted_and_stripped(self):
        structure = sample_structure()
        structure['format_version'] = FORMAT_VERSION
        normalized = StructureValidator().validate(structure)
        assert 'format_version' not in normalized

    def test_unknown_format_version_rejected(self):
        structure = sample_structure()
        structure['format_version'] = '9.9'
        with pytest.raises(SqlValidationError) as excinfo:
            StructureValidator().validate(structure)
        assert 'format_version' in str(excinfo.value)

    def test_missing_containers_are_normalized(self):
        """Older-format input (missing containers) is filled, not rejected."""
        structure = sample_structure()
        structure['root'].pop('event_triggers')
        structure['root'].pop('extensions')
        table = structure['root']['schemas']['alfa']['tables']['alfa_author']
        table.pop('indexes')
        normalized = StructureValidator().validate(structure)
        root = normalized['root']
        assert root['event_triggers'] == {}
        assert root['extensions'] == {}
        assert root['schemas']['alfa']['tables']['alfa_author']['indexes'] == {}

    def test_validate_returns_a_copy(self):
        structure = sample_structure()
        normalized = StructureValidator().validate(structure)
        normalized['root']['schemas'].clear()
        assert structure['root']['schemas']

    def test_pkey_column_must_exist(self):
        structure = sample_structure()
        table = structure['root']['schemas']['alfa']['tables']['alfa_author']
        table['attributes']['pkeys'] = 'missing_col'
        with pytest.raises(SqlValidationError) as excinfo:
            StructureValidator().validate(structure)
        assert "pkey column 'missing_col'" in str(excinfo.value)

    def test_unknown_dtype_rejected(self):
        structure = sample_structure()
        table = structure['root']['schemas']['alfa']['tables']['alfa_author']
        table['columns']['name']['attributes']['dtype'] = 'WRONG'
        with pytest.raises(SqlValidationError) as excinfo:
            StructureValidator().validate(structure)
        assert "unknown dtype 'WRONG'" in str(excinfo.value)

    def test_unknown_column_attribute_rejected(self):
        structure = sample_structure()
        table = structure['root']['schemas']['alfa']['tables']['alfa_author']
        table['columns']['name']['attributes']['name_long'] = 'Name'
        with pytest.raises(SqlValidationError) as excinfo:
            StructureValidator().validate(structure)
        assert "unknown attribute 'name_long'" in str(excinfo.value)

    def test_fk_target_must_exist(self):
        structure = sample_structure()
        recipe = structure['root']['schemas']['alfa']['tables']['alfa_recipe']
        relation = next(iter(recipe['relations'].values()))
        relation['attributes']['related_table'] = 'alfa_ghost'
        with pytest.raises(SqlValidationError) as excinfo:
            StructureValidator().validate(structure)
        assert "related_table 'alfa.alfa_ghost'" in str(excinfo.value)

    def test_fk_related_column_must_exist(self):
        structure = sample_structure()
        recipe = structure['root']['schemas']['alfa']['tables']['alfa_recipe']
        relation = next(iter(recipe['relations'].values()))
        relation['attributes']['related_columns'] = ['ghost_col']
        with pytest.raises(SqlValidationError) as excinfo:
            StructureValidator().validate(structure)
        assert "related column 'ghost_col'" in str(excinfo.value)

    def test_hashed_names_must_match_structure(self):
        structure = sample_structure()
        recipe = structure['root']['schemas']['alfa']['tables']['alfa_recipe']
        index_name, index_item = next(iter(recipe['indexes'].items()))
        index_item['entity_name'] = 'idx_deadbeef'
        recipe['indexes']['idx_deadbeef'] = recipe['indexes'].pop(index_name)
        with pytest.raises(SqlValidationError) as excinfo:
            StructureValidator().validate(structure)
        assert 'structural hash' in str(excinfo.value)

    def test_check_requires_clause(self):
        structure = sample_structure()
        recipe = structure['root']['schemas']['alfa']['tables']['alfa_recipe']
        recipe['constraints']['chk_recipe_rating']['attributes'].pop('check_clause')
        with pytest.raises(SqlValidationError) as excinfo:
            StructureValidator().validate(structure)
        assert 'check_clause' in str(excinfo.value)

    def test_all_errors_reported_together(self):
        structure = sample_structure()
        table = structure['root']['schemas']['alfa']['tables']['alfa_author']
        table['attributes']['pkeys'] = 'missing_col'
        table['columns']['name']['attributes']['dtype'] = 'WRONG'
        with pytest.raises(SqlValidationError) as excinfo:
            StructureValidator().validate(structure)
        assert len(excinfo.value.errors) >= 2


class TestFormatVersionEnvelope:
    """The migrator strips the envelope key before diffing."""

    def test_extract_orm_strips_format_version(self):
        from unittest.mock import MagicMock
        migrator = SqlMigrator(MagicMock())
        structure = sample_structure()
        structure['format_version'] = FORMAT_VERSION
        migrator.ormStructure = structure
        migrator.extractOrm()
        assert 'format_version' not in migrator.ormStructure
        assert migrator.ormStructure['root'] is structure['root']
