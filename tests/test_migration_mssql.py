"""Integration tests for the MSSQL dialect against SQL Server 2022.

Each scenario applies a migration and then re-runs
``prepareMigrationCommands`` asserting zero residual SQL (idempotence).
Direct pymssql queries verify the applied structure. The module is
skipped when Docker is unavailable (the ``mssql_server`` fixture skips).

Model: real schemas inside one database. The database
``test_gsm_mssql`` is created via the manager (``master``) connection,
then schemas ``alfa`` and ``beta`` live inside it.
"""

import pymssql
import pytest

from genro_sqlmigration import SqlMigrator

from .support.mssql_database import MssqlContainer, MssqlTestDatabase
from .support.orm_producer import OrmJsonProducer, SrcModel

DBNAME = 'test_gsm_mssql'


@pytest.fixture(scope='session')
def mssql_server():
    """Session-scoped SQL Server container; skips if Docker is unavailable."""
    container = MssqlContainer()
    if not container.docker_available():
        pytest.skip('Docker is not available for the MSSQL integration suite')
    container.start()
    try:
        yield container.conf()
    finally:
        container.stop()


@pytest.fixture(scope='class')
def mssql_env(request, mssql_server):
    """Bind the test class to a fresh database on the SQL Server instance."""
    cls = request.cls
    cls.conf = dict(mssql_server)
    cls.model = SrcModel()
    cls.src = cls.model
    cls.db = MssqlTestDatabase(DBNAME, cls.conf, cls.model)
    cls.producer = OrmJsonProducer(cls.model, DBNAME)
    cls.migrator = SqlMigrator(cls.db, removeDisabled=False)
    # Clean slate: drop the application database if a previous run left it.
    cls.db.dropDb(DBNAME)
    yield
    cls.db.closeConnection()
    cls.db.dropDb(DBNAME)


@pytest.mark.usefixtures('mssql_env')
class TestMssqlMigration:
    """End-to-end MSSQL migration scenarios on the live container."""

    def _apply(self):
        """Rebuild the ORM structure, prepare and apply the migration."""
        self.migrator.ormStructure = self.producer.get_json_struct()
        self.migrator.prepareMigrationCommands()
        changes = self.migrator.getChanges()
        self.migrator.applyChanges()
        return changes

    def _rediff(self):
        """Re-run the migrator and return residual SQL (empty when idempotent)."""
        self.migrator.ormStructure = self.producer.get_json_struct()
        self.migrator.prepareMigrationCommands()
        return self.migrator.getChanges()

    def _query(self, sql):
        conf = self.conf
        connection = pymssql.connect(
            server=conf['server'], port=conf['port'],
            user=conf['user'], password=conf['password'], database=DBNAME,
        )
        try:
            with connection.cursor() as cur:
                cur.execute('SET QUOTED_IDENTIFIER ON')
                cur.execute(sql)
                return cur.fetchall()
        finally:
            connection.close()

    def test_01_create_from_empty(self):
        """Two schemas, tables with pk, the type map, an FK, multi-UNIQUE, indexes."""
        alfa = self.src.package('alfa', sqlschema='alfa')
        beta = self.src.package('beta', sqlschema='beta')

        users = alfa.table('users', pkey='id')
        users.column('id', dtype='serial')
        users.column('name', size=':80')

        doc = alfa.table('doc', pkey='id')
        doc.column('id', dtype='serial')
        doc.column('title', size=':120')
        doc.column('body', dtype='T')
        doc.column('qty', dtype='I')
        doc.column('big', dtype='L')
        doc.column('ratio', dtype='R')
        doc.column('price', dtype='N', size='10,2')
        doc.column('active', dtype='B')
        doc.column('day', dtype='D')
        doc.column('moment', dtype='DH')
        doc.column('stamp', dtype='DHZ')
        doc.column('clock', dtype='H')
        doc.column('payload', dtype='O')
        # FK doc.owner -> users.id (must match the int-identity serial pk type)
        doc.column('owner', dtype='I').relation('users.id', mode='foreignkey')
        # multi-column UNIQUE via composite column
        doc.column('a', size=':10')
        doc.column('b', size=':10')
        doc.compositeColumn('ab', columns='a,b', unique=True)
        # a filtered index (WHERE) on a plain column; the predicate is
        # written in SQL Server's canonical stored form ("([col] IS NOT
        # NULL)") so introspection reads it back verbatim and the diff is
        # idempotent (SQL Server rewrites free-form predicates on store).
        doc.column('label', size=':40', indexed={'where': '([label] IS NOT NULL)'})

        item = beta.table('item', pkey='id')
        item.column('id', dtype='serial')
        item.column('code', size=':30', indexed=True)

        self._apply()
        residual = self._rediff()
        assert not residual, f"not idempotent: {residual}"

        # Schemas exist inside the database
        schemas = {r[0] for r in self._query(
            "SELECT name FROM sys.schemas"
        )}
        assert {'alfa', 'beta'}.issubset(schemas)

        # doc columns present
        cols = {r[0] for r in self._query(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA='alfa' AND TABLE_NAME='alfa_doc'"
        )}
        assert {'title', 'price', 'active', 'stamp', 'owner'}.issubset(cols)

        # FK exists on alfa_doc referencing alfa_users
        fks = self._query(
            "SELECT fk.name FROM sys.foreign_keys fk "
            "JOIN sys.tables t ON t.object_id = fk.parent_object_id "
            "JOIN sys.schemas s ON s.schema_id = t.schema_id "
            "JOIN sys.tables rt ON rt.object_id = fk.referenced_object_id "
            "WHERE s.name='alfa' AND t.name='alfa_doc' AND rt.name='alfa_users'"
        )
        assert fks

        # multi-column UNIQUE constraint on (a, b)
        uniques = self._query(
            "SELECT CONSTRAINT_NAME FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS "
            "WHERE TABLE_SCHEMA='alfa' AND TABLE_NAME='alfa_doc' "
            "AND CONSTRAINT_TYPE='UNIQUE'"
        )
        assert uniques

        # plain index on beta.item.code
        item_indexes = self._query(
            "SELECT i.name FROM sys.indexes i "
            "JOIN sys.tables t ON t.object_id = i.object_id "
            "JOIN sys.schemas s ON s.schema_id = t.schema_id "
            "WHERE s.name='beta' AND t.name='beta_item' "
            "AND i.is_primary_key = 0 AND i.name IS NOT NULL"
        )
        assert item_indexes

        # filtered index on alfa.doc.label (has_filter = 1)
        filtered = self._query(
            "SELECT i.name FROM sys.indexes i "
            "JOIN sys.tables t ON t.object_id = i.object_id "
            "JOIN sys.schemas s ON s.schema_id = t.schema_id "
            "WHERE s.name='alfa' AND t.name='alfa_doc' AND i.has_filter = 1"
        )
        assert filtered

    def test_02_add_column(self):
        doc = self.src.table('alfa.doc')
        doc.column('note', dtype='T')
        self._apply()
        assert not self._rediff()
        cols = {r[0] for r in self._query(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA='alfa' AND TABLE_NAME='alfa_doc'"
        )}
        assert 'note' in cols

    def test_03_add_index(self):
        doc = self.src.table('alfa.doc')
        doc.column('title', size=':120', indexed=True)
        self._apply()
        assert not self._rediff()
        indexes = self._query(
            "SELECT i.name FROM sys.indexes i "
            "JOIN sys.tables t ON t.object_id = i.object_id "
            "JOIN sys.schemas s ON s.schema_id = t.schema_id "
            "JOIN sys.index_columns ic "
            "  ON ic.object_id = i.object_id AND ic.index_id = i.index_id "
            "JOIN sys.columns c "
            "  ON c.object_id = ic.object_id AND c.column_id = ic.column_id "
            "WHERE s.name='alfa' AND t.name='alfa_doc' AND c.name='title' "
            "AND i.is_primary_key = 0"
        )
        assert indexes

    def test_04_add_fk_to_existing_table(self):
        item = self.src.table('beta.item')
        item.column('doc_id', dtype='I').relation('alfa.doc.id', mode='foreignkey')
        self._apply()
        assert not self._rediff()
        fks = self._query(
            "SELECT fk.name FROM sys.foreign_keys fk "
            "JOIN sys.tables t ON t.object_id = fk.parent_object_id "
            "JOIN sys.schemas s ON s.schema_id = t.schema_id "
            "JOIN sys.tables rt ON rt.object_id = fk.referenced_object_id "
            "WHERE s.name='beta' AND t.name='beta_item' AND rt.name='alfa_doc'"
        )
        assert fks

    def test_05_stripped_attributes(self):
        """A deferrable FK and a column comment: both attributes stripped."""
        alfa = self.src.package('alfa')
        parent = alfa.table('cat', pkey='id')
        parent.column('id', dtype='serial')
        child = alfa.table('kid', pkey='id')
        child.column('id', dtype='serial')
        # deferrable FK (fk_deferrable is not a capability -> stripped)
        child.column('cat_id', dtype='I').relation(
            'alfa.cat.id', mode='foreignkey', deferrable=True
        )
        # a column comment (comments is not a capability -> stripped)
        child.column('label', size=':40', comment='a column comment')

        self.migrator.ormStructure = self.producer.get_json_struct()
        self.migrator.prepareMigrationCommands()
        changes = self.migrator.getChanges()
        self.migrator.applyChanges()

        warnings = self.migrator.warnings
        assert any('fk_deferrable' in w for w in warnings), warnings
        assert any('comments' in w for w in warnings), warnings
        # stripped attributes must not leak into the emitted SQL
        assert 'DEFERRABLE' not in changes
        assert 'COMMENT' not in changes
        assert not self._rediff()

    def test_06_enlarge_nvarchar(self):
        """Widen a nullable, default-less nvarchar via ALTER COLUMN."""
        doc = self.src.table('alfa.doc')
        doc.column('title', size=':250', indexed=True)
        self._apply()
        assert not self._rediff()
        length = self._query(
            "SELECT CHARACTER_MAXIMUM_LENGTH FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA='alfa' AND TABLE_NAME='alfa_doc' "
            "AND COLUMN_NAME='title'"
        )
        assert length[0][0] == 250
