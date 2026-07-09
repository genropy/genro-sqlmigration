"""Wave 0 oracle tests: CHECK constraints and column/table comments.

These are NEW oracles (no legacy counterpart: the legacy engine dropped
CHECK constraints at extraction time and had no comment support). They
pin the wave-0 contract additions of roadmap doc ``05`` §2:

- CHECK constraints are name-keyed entities with a ``check_clause``
  attribute; producers write the clause as PostgreSQL prints it
  (e.g. ``(rating >= 0)``) so it compares clean against introspection
  (interim rule until the wave-1 canonicalization probe).
- ``comment`` is a column attribute (9th ``COL_JSON_KEYS`` entry) and a
  table attribute; ``COMMENT ON`` statements are idempotent replaces
  emitted after the table's other commands.

Same three-layer discipline as the M1 suite: exact SQL -> apply ->
idempotence re-diff.
"""

import psycopg
import pytest

from .support.migration_base import BaseMigrationTest


@pytest.mark.postgresql
@pytest.mark.usefixtures('migration_env')
class TestCheckConstraints(BaseMigrationTest):
    """CHECK constraints: inline, added, changed clause, violating data."""

    dbname = 'test_gnrsqlmigration_check'

    def test_check_01_create_db(self):
        self.checkChanges(apply_only=True)

    def test_check_02_create_table_with_inline_check(self):
        """CHECK declared with the table goes inline in CREATE TABLE."""
        pkg = self.src.package('alfa', sqlschema='alfa')
        tbl = pkg.table('product', pkey='id')
        tbl.column('id', dtype='serial')
        tbl.column('rating', dtype='I')
        tbl.checkConstraint('chk_product_rating', '(rating >= 0)')
        check_value = (
            'CREATE SCHEMA "alfa";\n'
            'CREATE TABLE "alfa"."alfa_product"(\n'
            ' "id" serial8 NOT NULL,\n'
            ' "rating" integer,\n'
            ' PRIMARY KEY (id),\n'
            ' CONSTRAINT "chk_product_rating" CHECK ((rating >= 0))\n'
            ');'
        )
        self.checkChanges(check_value)

    def test_check_03_add_check_to_existing_table(self):
        """A new CHECK becomes ALTER TABLE ADD CONSTRAINT."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('product')
        tbl.column('qty', dtype='I')
        tbl.checkConstraint('chk_product_qty', '(qty >= 0)')
        check_value = (
            'ALTER TABLE "alfa"."alfa_product"\n'
            'ADD COLUMN "qty" integer;\n'
            'ALTER TABLE "alfa"."alfa_product"\n'
            'ADD CONSTRAINT "chk_product_qty" CHECK ((qty >= 0));'
        )
        self.checkChanges(check_value)

    def test_check_04_change_clause(self):
        """A clause change is DROP CONSTRAINT + ADD CONSTRAINT."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('product')
        tbl.checkConstraint('chk_product_rating', '(rating > 0)')
        check_value = (
            'ALTER TABLE "alfa"."alfa_product"\n'
            'DROP CONSTRAINT chk_product_rating;\n'
            'ALTER TABLE "alfa"."alfa_product"\n'
            'ADD CONSTRAINT "chk_product_rating" CHECK ((rating > 0));'
        )
        self.checkChanges(check_value)

    def test_check_05_violating_data_surfaces_error(self):
        """Adding a CHECK that existing data violates raises loudly."""
        self.db.execute(
            "INSERT INTO alfa.alfa_product (rating, qty) VALUES (5, 3)"
        )
        self.db.commit()
        self.db.closeConnection()
        pkg = self.src.package('alfa')
        tbl = pkg.table('product')
        tbl.checkConstraint('chk_product_qty', '(qty > 100)')
        self.startup()
        self.migrator.prepareMigrationCommands()
        with pytest.raises(psycopg.errors.CheckViolation):
            self.migrator.applyChanges()


@pytest.mark.postgresql
@pytest.mark.usefixtures('migration_env')
class TestComments(BaseMigrationTest):
    """Column and table comments: add, change, remove (idempotent replace)."""

    dbname = 'test_gnrsqlmigration_comments'

    def test_comment_01_create_db(self):
        self.checkChanges(apply_only=True)

    def test_comment_02_create_table_with_comments(self):
        """Table and column comments follow the CREATE TABLE."""
        pkg = self.src.package('alfa', sqlschema='alfa')
        tbl = pkg.table('movie', pkey='id', comment='Movies catalog')
        tbl.column('id', dtype='serial')
        tbl.column('title', comment="The movie's title")
        check_value = (
            'CREATE SCHEMA "alfa";\n'
            'CREATE TABLE "alfa"."alfa_movie"(\n'
            ' "id" serial8 NOT NULL,\n'
            ' "title" text,\n'
            ' PRIMARY KEY (id)\n'
            ');\n'
            'COMMENT ON TABLE "alfa"."alfa_movie" IS \'Movies catalog\';\n'
            'COMMENT ON COLUMN "alfa"."alfa_movie"."title" IS \'The movie\'\'s title\';'
        )
        self.checkChanges(check_value)

    def test_comment_03_add_column_with_comment(self):
        pkg = self.src.package('alfa')
        tbl = pkg.table('movie')
        tbl.column('year', dtype='I', comment='Release year')
        check_value = (
            'ALTER TABLE "alfa"."alfa_movie"\n'
            'ADD COLUMN "year" integer;\n'
            'COMMENT ON COLUMN "alfa"."alfa_movie"."year" IS \'Release year\';'
        )
        self.checkChanges(check_value)

    def test_comment_04_change_column_comment(self):
        pkg = self.src.package('alfa')
        tbl = pkg.table('movie')
        tbl.column('year').attributes['comment'] = 'Year of first release'
        check_value = (
            'COMMENT ON COLUMN "alfa"."alfa_movie"."year" '
            'IS \'Year of first release\';'
        )
        self.checkChanges(check_value)

    def test_comment_05_remove_column_comment(self):
        pkg = self.src.package('alfa')
        tbl = pkg.table('movie')
        tbl.column('year').attributes.pop('comment')
        check_value = 'COMMENT ON COLUMN "alfa"."alfa_movie"."year" IS NULL;'
        self.checkChanges(check_value)

    def test_comment_06_change_table_comment(self):
        pkg = self.src.package('alfa')
        tbl = pkg.table('movie')
        tbl.attributes['comment'] = 'Catalog of movies'
        check_value = 'COMMENT ON TABLE "alfa"."alfa_movie" IS \'Catalog of movies\';'
        self.checkChanges(check_value)
