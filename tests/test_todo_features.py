"""Intended-but-unimplemented features, ported from the legacy ``ToDo``
class (dormant via the missing ``Test`` prefix; here dormant via explicit
``skip`` markers with a reason, as planned in roadmap doc ``03`` §3).

They document the expected behavior of explicitly-named FK/UNIQUE
add/drop operations and primary-key changes. Activate them one by one
when the corresponding feature lands.
"""

import pytest

from .support.migration_base import BaseMigrationTest

UNIMPLEMENTED = pytest.mark.skip(
    reason='documented future feature, not implemented (legacy ToDo)'
)


@pytest.mark.postgresql
@pytest.mark.usefixtures('migration_env')
class TestTodoFeatures(BaseMigrationTest):

    dbname = 'test_gnrsqlmigration_todo'

    @UNIMPLEMENTED
    def test_11_add_foreign_key(self):
        """Explicitly-named FK on a non-PK target with supporting index."""
        pkg = self.src.package('alfa')
        tbl_ingredient = pkg.table('ingredient')
        tbl_ingredient.column('recipe_id', dtype='integer').relation(
            'alfa.recipe.id', mode='foreignkey'
        )
        check_value = (
            'ALTER TABLE "alfa"."alfa_ingredient" ADD CONSTRAINT fk_recipe FOREIGN KEY ("recipe_id") '
            'REFERENCES "alfa"."alfa_recipe" ("id");\n'
            'CREATE INDEX ON "alfa"."alfa_ingredient" ("recipe_id");'
        )
        self.checkChanges(check_value)

    @UNIMPLEMENTED
    def test_12_drop_foreign_key(self):
        """Dropping a foreign key constraint from a table."""
        pkg = self.src.package('alfa')
        tbl_ingredient = pkg.table('ingredient')
        tbl_ingredient.drop_foreign_key('fk_recipe')
        check_value = 'ALTER TABLE "alfa"."alfa_ingredient" DROP CONSTRAINT fk_recipe;'
        self.checkChanges(check_value)

    @UNIMPLEMENTED
    def test_13_add_unique_constraint(self):
        """Adding an explicitly-named unique constraint on a column."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('recipe')
        tbl.unique_constraint(['code'])
        check_value = 'ALTER TABLE "alfa"."alfa_recipe" ADD CONSTRAINT unique_code UNIQUE ("code");'
        self.checkChanges(check_value)

    @UNIMPLEMENTED
    def test_14_drop_unique_constraint(self):
        """Dropping an explicitly-named unique constraint."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('recipe')
        tbl.drop_constraint('unique_code')
        check_value = 'ALTER TABLE "alfa"."alfa_recipe" DROP CONSTRAINT unique_code;'
        self.checkChanges(check_value)

    @UNIMPLEMENTED
    def test_10b_change_pkey(self):
        """Changing the primary key of an existing table."""
