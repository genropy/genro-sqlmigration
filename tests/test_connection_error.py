"""Connection-error taxonomy tests (#655 backport, legacy issue #654).

An unreachable database server must surface as a clear
:class:`SqlConnectionException` and must never be mistaken for a
non-existing database (which would trigger a CREATE DATABASE attempt).

Ported from the legacy ``gnrpy/tests/sql/test_connection_error.py`` with
two adaptations:

- the adapter-level tests target the test-support ``PgTestAdapter``
  (the package delegates ``connect()`` to the consumer's adapter; this
  is the reference implementation for the producer guide);
- the migrator-level test asserts the exception PROPAGATES: the legacy
  ``SystemExit`` is wrong for a library (roadmap/02 §A.4 decision).
"""

from unittest.mock import MagicMock, patch

import psycopg
import pytest

from genro_sqlmigration import SqlMigrator
from genro_sqlmigration.exceptions import (
    NonExistingDbException,
    SqlConnectionException,
)
from genro_sqlmigration.readers import PgReader

from .support.pg_database import PgTestAdapter


def _adapter(dbname='test_db'):
    database = MagicMock()
    database.get_dbname.return_value = dbname
    database.connection_params.return_value = {
        'host': 'localhost', 'dbname': dbname,
    }
    return PgTestAdapter(database)


class TestAdapterConnectionError:
    """The adapter distinguishes connection errors from a missing database."""

    def test_nonexisting_db_raises_nonexisting_exception(self):
        adapter = _adapter('nonexistent_db')
        error = psycopg.OperationalError(
            'FATAL:  database "nonexistent_db" does not exist'
        )
        with patch('psycopg.connect', side_effect=error), \
                pytest.raises(NonExistingDbException):
            adapter.connect()

    def test_unreachable_server_raises_connection_exception(self):
        adapter = _adapter('test_db')
        error = psycopg.OperationalError(
            'connection to server at "nonexistent-host.invalid", port 5432 '
            'failed: Connection refused'
        )
        with patch('psycopg.connect', side_effect=error):
            with pytest.raises(SqlConnectionException) as exc_info:
                adapter.connect()
            assert exc_info.value.dbname == 'test_db'
            assert exc_info.value.original_error is error

    def test_connection_exception_has_clear_message(self):
        exc = SqlConnectionException(
            'mydb',
            original_error=Exception('Connection refused'),
        )
        msg = str(exc)
        assert 'mydb' in msg
        assert 'Connection refused' in msg


class TestReaderConnectionError:
    """get_json_struct: missing DB -> {}; connection error -> re-raised."""

    def _reader(self, connect_side_effect):
        reader = PgReader(connection_params={'dbname': 'mydb'})
        reader.connect = MagicMock(side_effect=connect_side_effect)
        return reader

    def test_nonexisting_db_returns_empty(self):
        reader = self._reader(NonExistingDbException('mydb'))
        assert reader.get_json_struct('mydb', schemas=['alfa']) == {}

    def test_connection_error_propagates(self):
        conn_error = SqlConnectionException(
            'mydb', original_error=Exception('No route to host')
        )
        reader = self._reader(conn_error)
        with pytest.raises(SqlConnectionException) as exc_info:
            reader.get_json_struct('mydb', schemas=['alfa'])
        assert exc_info.value is conn_error


class TestMigratorConnectionError:
    """prepareMigrationCommands lets the connection error reach the caller.

    The legacy migrator converted it into ``SystemExit`` — wrong for a
    library: the consumer decides how to abort.
    """

    def test_migrator_propagates_connection_error(self):
        migrator = MagicMock(spec=SqlMigrator)
        migrator.prepareMigrationCommands = (
            SqlMigrator.prepareMigrationCommands.__get__(migrator)
        )
        conn_error = SqlConnectionException(
            'mydb', original_error=Exception('No route to host')
        )
        migrator.prepareStructures.side_effect = conn_error

        with pytest.raises(SqlConnectionException) as exc_info:
            migrator.prepareMigrationCommands()
        msg = str(exc_info.value)
        assert 'mydb' in msg
        assert 'No route to host' in msg
