"""Integration tests for the MySQL dialect against an ephemeral mysql:8.

Each scenario applies a migration and then re-runs
``prepareMigrationCommands`` asserting zero residual SQL (idempotence).
Direct pymysql queries verify the applied structure. The module is
skipped when Docker is unavailable.
"""

import pymysql
import pytest

from genro_sqlmigration import SqlMigrator

from .support.mysql_database import (
    MysqlTestDatabase,
    start_mysql_container,
    stop_mysql_container,
)
from .support.orm_producer import OrmJsonProducer, SrcModel

DBNAME = 'test_gsm_mysql'


@pytest.fixture(scope='module')
def mysql_server():
    """Start an ephemeral mysql:8 container for the module."""
    conf, container_id = start_mysql_container()
    yield conf
    stop_mysql_container(container_id)


@pytest.fixture(scope='class')
def mysql_env(request, mysql_server):
    """Bind the test class to a fresh database on the MySQL server."""
    cls = request.cls
    conf = dict(mysql_server, dbname=DBNAME)
    cls.conf = conf
    cls.model = SrcModel()
    cls.src = cls.model
    cls.db = MysqlTestDatabase(conf, cls.model)
    cls.producer = OrmJsonProducer(cls.model, DBNAME)
    cls.migrator = SqlMigrator(cls.db, removeDisabled=False)
    # Clean slate: the app database plus the schema-databases used below.
    for schema in (DBNAME, 'alfa', 'beta'):
        cls.db.dropDb(schema)
    yield
    cls.db.closeConnection()
    for schema in (DBNAME, 'alfa', 'beta'):
        cls.db.dropDb(schema)


@pytest.mark.usefixtures('mysql_env')
class TestMysqlMigration:
    """End-to-end MySQL migration scenarios on the live container."""

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
        connection = pymysql.connect(
            host=conf['host'], port=conf['port'],
            user=conf['user'], password=conf['password'],
        )
        try:
            with connection.cursor() as cur:
                cur.execute("SET SESSION sql_mode = CONCAT(@@sql_mode, ',ANSI_QUOTES')")
                cur.execute(sql)
                return cur.fetchall()
        finally:
            connection.close()

    def test_01_create_from_empty(self):
        """Two schemas, tables with pk, the type map, an FK, multi-UNIQUE, an index."""
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
        # FK doc.owner -> users.id (must match the bigint serial pk type)
        doc.column('owner', dtype='L').relation('users.id', mode='foreignkey')
        # multi-column UNIQUE via composite column
        doc.column('a', size=':10')
        doc.column('b', size=':10')
        doc.compositeColumn('ab', columns='a,b', unique=True)

        item = beta.table('item', pkey='id')
        item.column('id', dtype='serial')
        item.column('code', size=':30', indexed=True)

        self._apply()
        residual = self._rediff()
        assert not residual, f"not idempotent: {residual}"

        # Databases (schemas) exist
        schemas = {r[0] for r in self._query(
            "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA"
        )}
        assert {'alfa', 'beta'}.issubset(schemas)

        # doc columns present
        cols = {r[0] for r in self._query(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA='alfa' AND TABLE_NAME='alfa_doc'"
        )}
        assert {'title', 'price', 'active', 'stamp', 'owner'}.issubset(cols)

        # FK exists on alfa_doc
        fks = self._query(
            "SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA='alfa' AND TABLE_NAME='alfa_doc' "
            "AND REFERENCED_TABLE_NAME='alfa_users'"
        )
        assert fks

        # multi-column UNIQUE constraint on (a, b)
        uniques = self._query(
            "SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS "
            "WHERE TABLE_SCHEMA='alfa' AND TABLE_NAME='alfa_doc' "
            "AND CONSTRAINT_TYPE='UNIQUE'"
        )
        assert uniques

        # index on beta.item.code
        indexes = self._query(
            "SELECT DISTINCT INDEX_NAME FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA='beta' AND TABLE_NAME='beta_item' "
            "AND INDEX_NAME <> 'PRIMARY'"
        )
        assert indexes

    def test_02_add_column(self):
        doc = self.src.table('alfa.doc')
        doc.column('note', dtype='T')
        self._apply()
        assert not self._rediff()
        cols = {r[0] for r in self._query(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA='alfa' AND TABLE_NAME='alfa_doc'"
        )}
        assert 'note' in cols

    def test_03_add_index(self):
        doc = self.src.table('alfa.doc')
        doc.column('title', size=':120', indexed=True)
        self._apply()
        assert not self._rediff()
        indexes = self._query(
            "SELECT DISTINCT INDEX_NAME FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA='alfa' AND TABLE_NAME='alfa_doc' "
            "AND COLUMN_NAME='title'"
        )
        assert indexes

    def test_04_add_fk_to_existing_table(self):
        item = self.src.table('beta.item')
        item.column('doc_id', dtype='L').relation('alfa.doc.id', mode='foreignkey')
        self._apply()
        assert not self._rediff()
        fks = self._query(
            "SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA='beta' AND TABLE_NAME='beta_item' "
            "AND REFERENCED_TABLE_NAME='alfa_doc'"
        )
        assert fks

    def test_05_stripped_attributes(self):
        """A deferrable FK and an index with WHERE: both attributes stripped."""
        alfa = self.src.package('alfa')
        parent = alfa.table('cat', pkey='id')
        parent.column('id', dtype='serial')
        child = alfa.table('kid', pkey='id')
        child.column('id', dtype='serial')
        # deferrable FK
        child.column('cat_id', dtype='L').relation(
            'alfa.cat.id', mode='foreignkey', deferrable=True
        )
        # index with a WHERE clause
        child.column('label', size=':40', indexed={'where': 'label IS NOT NULL'})

        self.migrator.ormStructure = self.producer.get_json_struct()
        self.migrator.prepareMigrationCommands()
        changes = self.migrator.getChanges()
        self.migrator.applyChanges()

        warnings = self.migrator.warnings
        assert any('fk_deferrable' in w for w in warnings), warnings
        assert any('index_where' in w for w in warnings), warnings
        # stripped attributes must not leak into the emitted SQL
        assert 'DEFERRABLE' not in changes
        assert 'WHERE' not in changes
        assert not self._rediff()

    def test_06_enlarge_varchar(self):
        """Widen a nullable, default-less varchar via MODIFY COLUMN."""
        doc = self.src.table('alfa.doc')
        doc.column('title', size=':250', indexed=True)
        self._apply()
        assert not self._rediff()
        length = self._query(
            "SELECT CHARACTER_MAXIMUM_LENGTH FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA='alfa' AND TABLE_NAME='alfa_doc' "
            "AND COLUMN_NAME='title'"
        )
        assert length[0][0] == 250
