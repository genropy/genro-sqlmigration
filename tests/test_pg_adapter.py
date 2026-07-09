"""Tests for the production PostgreSQL adapter/database pair."""

import pytest

from genro_sqlmigration import PgAdapter, PgDatabase
from genro_sqlmigration.exceptions import SqlMigrationError
from genro_sqlmigration.readers import PgReader


class TestPgDatabase:
    def _db(self, **kwargs):
        return PgDatabase({'dbname': 'mydb', 'host': 'localhost'}, **kwargs)

    def test_missing_dbname_raises(self):
        with pytest.raises(SqlMigrationError):
            PgDatabase({'host': 'localhost'})

    def test_schema_getters_return_injected_lists(self):
        db = self._db(application_schemas=['alfa', 'beta'],
                      read_only_schemas=['ro'], tenant_schemas=['t1'])
        assert db.getApplicationSchemas() == ['alfa', 'beta']
        assert db.readOnlySchemas() == ['ro']
        assert db.getTenantSchemas() == ['t1']
        assert db.get_dbname() == 'mydb'

    def test_connection_params_manager_swap(self):
        db = self._db()
        assert db.connection_params()['dbname'] == 'mydb'
        assert db.connection_params(manager=True)['dbname'] == 'postgres'
        assert db.connection_params()['dbname'] == 'mydb'


class TestPgAdapter:
    def test_reader_is_lazy_and_cached(self):
        db = PgDatabase({'dbname': 'mydb'})
        assert isinstance(db.adapter, PgAdapter)
        reader = db.adapter.reader
        assert isinstance(reader, PgReader)
        assert db.adapter.reader is reader
        assert reader.dbname() == 'mydb'
