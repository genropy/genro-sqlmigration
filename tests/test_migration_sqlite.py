"""Integration tests for the SQLite dialect on real database files.

Each scenario runs the full migrator against ``SqliteTestDatabase``
(the production ``SqliteAdapter``/``SqliteDatabase``) and asserts
idempotence: apply the changes, re-run ``prepareMigrationCommands`` and
assert no further SQL. Direct ``sqlite3`` inspection verifies the applied
structure.
"""

import sqlite3

from genro_sqlmigration import SqlMigrator

from .support.orm_producer import OrmJsonProducer, SrcModel
from .support.sqlite_database import SqliteTestDatabase


class SqliteMigration:
    """Small harness: model, database, producer and re-diffing migrator."""

    def __init__(self, tmp_path, schemas):
        self.model = SrcModel()
        self.db = SqliteTestDatabase(tmp_path, application_schemas=schemas)
        self.producer = OrmJsonProducer(self.model, self.db.get_dbname())
        self.db.dropDb()

    def _migrator(self):
        migrator = SqlMigrator(self.db, removeDisabled=False)
        migrator.ormStructure = self.producer.get_json_struct()
        migrator.prepareMigrationCommands()
        return migrator

    def apply(self):
        """Prepare and apply the migration; return (changes, warnings)."""
        migrator = self._migrator()
        changes = migrator.getChanges()
        migrator.applyChanges()
        return changes, migrator.warnings

    def diff(self):
        """Return (changes, warnings) without applying (for re-diff checks)."""
        migrator = self._migrator()
        return migrator.getChanges(), migrator.warnings

    def apply_and_assert_idempotent(self):
        changes, warnings = self.apply()
        residual, _ = self.diff()
        assert residual == '', f'migration not idempotent: {residual}'
        return changes, warnings

    def connect(self):
        conn = sqlite3.connect(self.db.get_dbname())
        for schema_name, path in self.db.schema_files().items():
            conn.execute(f'ATTACH DATABASE \'{path}\' AS "{schema_name}"')
        return conn


class TestCreateFromEmpty:
    def test_two_schemas_full_table(self, tmp_path):
        env = SqliteMigration(tmp_path, ['alfa', 'beta'])
        alfa = env.model.package('alfa')
        doc = alfa.table('doc', pkey='id')
        doc.column('id', dtype='I')
        doc.column('title', size='0:100', notnull=True)
        doc.column('body', dtype='T')
        doc.column('amount', dtype='N', size='10,2')
        doc.column('flag', dtype='B')
        doc.column('created', dtype='D')
        doc.column('code', size='0:50', unique=True)
        doc.column('slug', size='0:80', indexed=True)
        doc.column('ref', dtype='I', indexed={'where': 'ref IS NOT NULL'})
        beta = env.model.package('beta')
        note = beta.table('note', pkey='id')
        note.column('id', dtype='I')
        note.column('text', dtype='T')

        env.apply_and_assert_idempotent()

        conn = env.connect()
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM alfa.sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert tables == {'alfa_doc'}
        beta_tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM beta.sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert beta_tables == {'beta_note'}
        columns = [r[1] for r in conn.execute('PRAGMA alfa.table_info("alfa_doc")')]
        assert columns == [
            'id', 'title', 'body', 'amount', 'flag', 'created',
            'code', 'slug', 'ref',
        ]
        index_names = {
            r[0] for r in conn.execute(
                "SELECT name FROM alfa.sqlite_master WHERE type='index'"
            )
        }
        # plain index, partial index and the rewritten single-column unique
        assert 'idx_0bf5e76e' in index_names  # slug plain index
        assert any(n.startswith('cst_') for n in index_names)  # unique
        partial = conn.execute(
            "SELECT sql FROM alfa.sqlite_master WHERE type='index' "
            "AND sql LIKE '%WHERE%'"
        ).fetchall()
        assert partial and 'ref IS NOT NULL' in partial[0][0]
        conn.close()


class TestAddColumn:
    def test_add_column(self, tmp_path):
        env = SqliteMigration(tmp_path, ['alfa'])
        alfa = env.model.package('alfa')
        doc = alfa.table('doc', pkey='id')
        doc.column('id', dtype='I')
        doc.column('title', size='0:100', notnull=True)
        env.apply_and_assert_idempotent()

        doc.column('extra', dtype='T')
        changes, _ = env.apply_and_assert_idempotent()
        assert 'ADD COLUMN "extra" text' in changes

        conn = env.connect()
        columns = [r[1] for r in conn.execute('PRAGMA alfa.table_info("alfa_doc")')]
        assert 'extra' in columns
        conn.close()


class TestAddIndex:
    def test_add_index(self, tmp_path):
        env = SqliteMigration(tmp_path, ['alfa'])
        alfa = env.model.package('alfa')
        doc = alfa.table('doc', pkey='id')
        doc.column('id', dtype='I')
        doc.column('title', size='0:100', notnull=True)
        env.apply_and_assert_idempotent()

        doc.column('title').attributes['indexed'] = True
        changes, _ = env.apply_and_assert_idempotent()
        assert 'CREATE INDEX' in changes

        conn = env.connect()
        plain = conn.execute(
            "SELECT name FROM alfa.sqlite_master WHERE type='index' "
            "AND name LIKE 'idx_%'"
        ).fetchall()
        assert plain
        conn.close()


class TestForeignKeyStripped:
    def test_fk_stripped_with_warning(self, tmp_path):
        env = SqliteMigration(tmp_path, ['alfa'])
        alfa = env.model.package('alfa')
        users = alfa.table('users', pkey='id')
        users.column('id', dtype='I')
        posts = alfa.table('posts', pkey='id')
        posts.column('id', dtype='I')
        posts.column('user_id', dtype='I').relation(
            'users.id', mode='foreignkey'
        )

        changes, warnings = env.apply()
        assert 'FOREIGN KEY' not in changes
        assert 'REFERENCES' not in changes
        assert any('foreign_keys' in w for w in warnings)

        residual, _ = env.diff()
        assert residual == ''


class TestDtypeChangeStripped:
    def test_dtype_change_no_command(self, tmp_path):
        env = SqliteMigration(tmp_path, ['alfa'])
        alfa = env.model.package('alfa')
        t = alfa.table('t', pkey='id')
        t.column('id', dtype='I')
        t.column('val', dtype='I')
        env.apply_and_assert_idempotent()

        t.column('val').attributes['dtype'] = 'T'
        changes, warnings = env.diff()
        assert changes == ''
        assert any('alter_column_type' in w for w in warnings)
