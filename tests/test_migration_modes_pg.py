"""Migration modes on live PostgreSQL: default (exception), force, backup,
extensions.

Ported from the legacy ``BaseGnrSqlMigration_DefaultException`` /
``_ForceMode`` / ``_BackupMode`` / ``_Extension`` classes. Real data is
inserted and verified after conversion (invalid values -> NULL in force
mode; originals preserved in ``col__{dtype}`` backup columns in backup
mode). The incompatible-conversion exception is the package
``SqlMigrationError`` (legacy raised ``GnrSqlException``).
"""

import pytest

from genro_sqlmigration.exceptions import SqlMigrationError

from .support.migration_base import BaseMigrationTest
from .support.sqltools import normalize_sql


@pytest.mark.postgresql
@pytest.mark.usefixtures('migration_env')
class TestMigrationDefaultException(BaseMigrationTest):
    """Default mode: incompatible conversion on non-empty column raises."""

    dbname = 'test_gnrsqlmigration_exception'

    def test_exception_01_create_db(self):
        """Tests database creation."""
        check_value = f"""CREATE DATABASE "{self.dbname}" ENCODING 'UNICODE';\n"""
        self.startup()
        self.migrator.prepareMigrationCommands()
        changes = self.migrator.getChanges()
        assert normalize_sql(changes) == normalize_sql(check_value)
        self.migrator.applyChanges()

    def test_exception_02_create_table_with_data(self):
        """Creates test table and inserts data with invalid date format."""
        self.src.package('gamma', sqlschema='gamma')
        pkg = self.src.package('gamma')
        tbl = pkg.table('exception_test', pkey='id')
        tbl.column('id', dtype='serial')
        tbl.column('text_col')
        self.startup()
        self.migrator.prepareMigrationCommands()
        self.migrator.applyChanges()
        # Insert data that cannot be converted to date
        self.db.execute("INSERT INTO gamma.gamma_exception_test (text_col) VALUES ('not a date')")
        self.db.commit()

    def test_exception_03_incompatible_conversion_raises(self):
        """Default mode: exception on incompatible conversion, non-empty column."""
        pkg = self.src.package('gamma')
        tbl = pkg.table('exception_test', pkey='id')
        tbl.column('text_col', dtype='D')  # 'not a date' cannot become a date
        self.startup()
        with pytest.raises(SqlMigrationError) as excinfo:
            self.migrator.prepareMigrationCommands()
        # Exception message suggests both --force and --backup options
        assert '--force' in str(excinfo.value)
        assert '--backup' in str(excinfo.value)

    def test_exception_04_empty_column_converts(self):
        """Incompatible conversion on an EMPTY column proceeds."""
        pkg = self.src.package('gamma')
        tbl = pkg.table('exception_test', pkey='id')
        tbl.column('text_col').attributes['dtype'] = 'T'
        tbl.column('empty_col')
        self.new_migrator()
        self.startup()
        self.migrator.prepareMigrationCommands()
        self.migrator.applyChanges()

        # Convert empty column to date
        tbl.column('empty_col', dtype='D')
        self.startup()
        self.migrator.prepareMigrationCommands()
        changes = self.migrator.getChanges()
        assert 'ALTER COLUMN' in changes or 'TYPE date' in changes


@pytest.mark.postgresql
@pytest.mark.usefixtures('migration_env')
class TestMigrationForceMode(BaseMigrationTest):
    """Force mode: conversions proceed, non-matching values become NULL."""

    dbname = 'test_gnrsqlmigration_force'
    migrator_kwargs = {'removeDisabled': False, 'force': True}

    def test_force_01_create_db(self):
        """Tests database creation."""
        check_value = f"""CREATE DATABASE "{self.dbname}" ENCODING 'UNICODE';\n"""
        self.checkChanges(check_value)

    def test_force_02_create_schema_and_table(self):
        """Creates schema and test table with data."""
        self.src.package('delta', sqlschema='delta')
        pkg = self.src.package('delta')
        tbl = pkg.table('force_only_test', pkey='id')
        tbl.column('id', dtype='serial')
        tbl.column('text_col')
        self.checkChanges(apply_only=True)
        # Insert data with invalid date format
        self.db.execute("INSERT INTO delta.delta_force_only_test (text_col) VALUES ('not a date')")
        self.db.execute("INSERT INTO delta.delta_force_only_test (text_col) VALUES ('2024-01-15')")
        self.db.commit()

    def test_force_03_text_to_date_invalid_becomes_null(self):
        """Text to date conversion: invalid values become NULL."""
        pkg = self.src.package('delta')
        tbl = pkg.table('force_only_test', pkey='id')
        tbl.column('text_col', dtype='D')
        check_value = '''ALTER TABLE "delta"."delta_force_only_test"
ALTER COLUMN "text_col" TYPE date USING CASE WHEN "text_col" IS NULL THEN NULL WHEN "text_col" ~ \'^[0-9]{4}-[0-9]{2}-[0-9]{2}\' THEN "text_col"::date ELSE NULL END;'''
        self.startup()
        self.migrator.prepareMigrationCommands()
        changes = self.migrator.getChanges()
        assert normalize_sql(changes) == normalize_sql(check_value)
        self.migrator.applyChanges()
        self.db.closeConnection()
        # Verify: first row should be NULL, second should have the date
        result = self.db.execute(
            "SELECT text_col FROM delta.delta_force_only_test ORDER BY id"
        ).fetchall()
        assert result[0][0] is None  # 'not a date' -> NULL
        assert str(result[1][0]) == '2024-01-15'
        self.db.closeConnection()

    def test_force_04_add_and_convert_integer(self):
        """Text to integer conversion: invalid values become NULL."""
        pkg = self.src.package('delta')
        tbl = pkg.table('force_only_test', pkey='id')
        tbl.column('int_col')
        self.new_migrator()
        self.startup()
        self.migrator.prepareMigrationCommands()
        self.migrator.applyChanges()
        # Insert data with invalid integer format
        self.db.execute("UPDATE delta.delta_force_only_test SET int_col = 'abc' WHERE id = 1")
        self.db.execute("UPDATE delta.delta_force_only_test SET int_col = '42' WHERE id = 2")
        self.db.commit()
        self.db.closeConnection()

        tbl.column('int_col', dtype='I')
        check_value = '''ALTER TABLE "delta"."delta_force_only_test"
ALTER COLUMN "int_col" TYPE integer USING CASE WHEN "int_col" IS NULL THEN NULL WHEN "int_col" ~ \'^-?[0-9]+$\' THEN "int_col"::integer ELSE NULL END;'''
        self.new_migrator()
        self.startup()
        self.migrator.prepareMigrationCommands()
        changes = self.migrator.getChanges()
        assert normalize_sql(changes) == normalize_sql(check_value)
        self.migrator.applyChanges()
        self.db.closeConnection()
        # Verify: first row should be NULL, second should have 42
        result = self.db.execute(
            "SELECT int_col FROM delta.delta_force_only_test ORDER BY id"
        ).fetchall()
        assert result[0][0] is None  # 'abc' -> NULL
        assert result[1][0] == 42


@pytest.mark.postgresql
@pytest.mark.usefixtures('migration_env')
class TestMigrationBackupMode(BaseMigrationTest):
    """Backup mode: conversions create ``col__{dtype}`` backup columns."""

    dbname = 'test_gnrsqlmigration_backup'
    migrator_kwargs = {'removeDisabled': False, 'backup': True}

    def test_backup_01_create_db(self):
        """Tests database creation (same as default mode)."""
        check_value = """CREATE DATABASE "test_gnrsqlmigration_backup" ENCODING 'UNICODE';\n"""
        self.checkChanges(check_value)

    def test_backup_02_create_schema_and_table(self):
        """Creates schema and test table with data."""
        self.src.package('beta', sqlschema='beta')
        pkg = self.src.package('beta')
        tbl = pkg.table('backup_test', pkey='id')
        tbl.column('id', dtype='serial')
        tbl.column('text_col')
        self.checkChanges(apply_only=True)
        # Insert data with invalid date format
        self.db.execute("INSERT INTO beta.beta_backup_test (text_col) VALUES ('not a date')")
        self.db.execute("INSERT INTO beta.beta_backup_test (text_col) VALUES ('2024-01-15')")
        self.db.commit()

    def test_backup_03_text_to_date_with_backup(self):
        """Text to date conversion with backup: original data preserved."""
        pkg = self.src.package('beta')
        tbl = pkg.table('backup_test', pkey='id')
        tbl.column('text_col', dtype='D')
        check_value = '''ALTER TABLE "beta"."beta_backup_test" ADD COLUMN "text_col__T" text;
UPDATE "beta"."beta_backup_test" SET "text_col__T" = "text_col"::text;
ALTER TABLE "beta"."beta_backup_test"
ALTER COLUMN "text_col" TYPE date USING CASE WHEN "text_col" IS NULL THEN NULL WHEN "text_col" ~ \'^[0-9]{4}-[0-9]{2}-[0-9]{2}\' THEN "text_col"::date ELSE NULL END;'''
        self.startup()
        self.migrator.prepareMigrationCommands()
        changes = self.migrator.getChanges()
        assert normalize_sql(changes) == normalize_sql(check_value)
        self.migrator.applyChanges()
        self.db.closeConnection()
        # Verify: backup column has original data, converted column NULL/date
        result = self.db.execute(
            'SELECT text_col, "text_col__T" FROM beta.beta_backup_test ORDER BY id'
        ).fetchall()
        assert result[0][0] is None  # 'not a date' -> NULL
        assert result[0][1] == 'not a date'  # backup has original
        assert str(result[1][0]) == '2024-01-15'
        assert result[1][1] == '2024-01-15'  # backup has original
        self.db.closeConnection()

    def test_backup_04_add_integer_col(self):
        """Add integer column with data for the next conversion test."""
        pkg = self.src.package('beta')
        tbl = pkg.table('backup_test', pkey='id')
        tbl.column('int_source')
        # skip_recheck: the text_col__T backup column exists only in the DB
        self.checkChanges(apply_only=True)
        self.db.closeConnection()
        # Insert invalid integer data
        self.db.execute("UPDATE beta.beta_backup_test SET int_source = 'invalid' WHERE id = 1")
        self.db.execute("UPDATE beta.beta_backup_test SET int_source = '123' WHERE id = 2")
        self.db.commit()

    def test_backup_05_text_to_integer_with_backup(self):
        """Text to integer conversion with backup: original data preserved."""
        pkg = self.src.package('beta')
        tbl = pkg.table('backup_test', pkey='id')
        tbl.column('int_source', dtype='I')
        check_value = '''ALTER TABLE "beta"."beta_backup_test" ADD COLUMN "int_source__T" text;
UPDATE "beta"."beta_backup_test" SET "int_source__T" = "int_source"::text;
ALTER TABLE "beta"."beta_backup_test"
ALTER COLUMN "int_source" TYPE integer USING CASE WHEN "int_source" IS NULL THEN NULL WHEN "int_source" ~ \'^-?[0-9]+$\' THEN "int_source"::integer ELSE NULL END;'''
        self.startup()
        self.migrator.prepareMigrationCommands()
        changes = self.migrator.getChanges()
        assert normalize_sql(changes) == normalize_sql(check_value)
        self.migrator.applyChanges()
        self.db.closeConnection()
        # Verify: backup column has original data
        result = self.db.execute(
            'SELECT int_source, "int_source__T" FROM beta.beta_backup_test ORDER BY id'
        ).fetchall()
        assert result[0][0] is None  # 'invalid' -> NULL
        assert result[0][1] == 'invalid'  # backup has original
        assert result[1][0] == 123
        assert result[1][1] == '123'  # backup has original
        self.db.closeConnection()

    def test_backup_06_add_boolean_col(self):
        """Add text column for the boolean conversion test."""
        pkg = self.src.package('beta')
        tbl = pkg.table('backup_test', pkey='id')
        tbl.column('bool_source')
        self.checkChanges(apply_only=True)

    def test_backup_07_text_to_boolean_with_backup(self):
        """Text to boolean conversion with backup column."""
        pkg = self.src.package('beta')
        tbl = pkg.table('backup_test', pkey='id')
        tbl.column('bool_source', dtype='B')
        check_value = '''ALTER TABLE "beta"."beta_backup_test" ADD COLUMN "bool_source__T" text;
UPDATE "beta"."beta_backup_test" SET "bool_source__T" = "bool_source"::text;
ALTER TABLE "beta"."beta_backup_test"
ALTER COLUMN "bool_source" TYPE boolean USING CASE WHEN "bool_source" IS NULL THEN NULL WHEN LOWER("bool_source") IN (\'true\', \'t\', \'yes\', \'y\', \'1\') THEN TRUE WHEN LOWER("bool_source") IN (\'false\', \'f\', \'no\', \'n\', \'0\', \'\') THEN FALSE ELSE NULL END;'''
        # skip_recheck because backup columns are intentionally not in the model
        self.checkChanges(check_value, skip_recheck=True)

    def test_backup_08_any_to_text_no_backup(self):
        """Any-to-text conversion has no backup (lossless conversion)."""
        pkg = self.src.package('beta')
        tbl = pkg.table('backup_test', pkey='id')
        tbl.column('numeric_col', dtype='N', size='10,2')
        self.checkChanges(apply_only=True)

        # Convert numeric to text - no backup needed
        numeric_col = tbl.column('numeric_col', dtype='T')
        numeric_col.attributes.pop('size', None)
        check_value = 'ALTER TABLE "beta"."beta_backup_test" \n ALTER COLUMN "numeric_col" TYPE text;'
        # skip_recheck because backup columns from previous tests are in the DB
        self.checkChanges(check_value, skip_recheck=True)


@pytest.mark.postgresql
@pytest.mark.usefixtures('migration_env')
class TestMigrationExtension(BaseMigrationTest):
    """PostgreSQL EXTENSION handling in the migrator.

    Configured extensions generate ``CREATE EXTENSION IF NOT EXISTS``
    (never DROP + CREATE), and subsequent migrations do not recreate an
    already-installed extension.
    """

    dbname = 'test_gnrsqlmigration_ext'
    migrator_kwargs = {'extensions': 'pg_trgm', 'removeDisabled': False}

    def test_ext_00_create_db(self):
        self.startup()
        self.migrator.prepareMigrationCommands()
        changes = self.migrator.getChanges()
        assert 'CREATE DATABASE "test_gnrsqlmigration_ext"' in changes
        self.migrator.applyChanges()

    def test_ext_01_create_db_and_extension(self):
        """First migration: CREATE EXTENSION IF NOT EXISTS, never DROP."""
        self.startup()
        self.migrator.prepareMigrationCommands()
        changes = self.migrator.getChanges()
        assert 'DROP EXTENSION' not in changes
        assert 'CREATE EXTENSION IF NOT EXISTS pg_trgm;' in changes
        self.migrator.applyChanges()

    def test_ext_02_extension_not_recreated(self):
        """After install, migrations do not re-issue CREATE EXTENSION."""
        self.src.package('ext_pkg', sqlschema='ext_pkg')
        self.startup()
        self.migrator.prepareMigrationCommands()
        changes = self.migrator.getChanges()
        assert 'CREATE EXTENSION' not in changes, (
            'Already-installed extension must not be recreated'
        )
        assert 'DROP EXTENSION' not in changes
        self.migrator.applyChanges()
