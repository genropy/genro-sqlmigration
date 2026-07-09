"""Shared fixtures: ephemeral PostgreSQL server and per-class migration env.

PostgreSQL resolution order (as in the legacy suite):

1. ``GNR_TEST_PG_PASSWORD`` set -> use the configured server
   (``GNR_TEST_PG_HOST``/``GNR_TEST_PG_PORT``/``GNR_TEST_PG_USER``).
2. Otherwise start a session-scoped ``testing.postgresql`` instance
   (requires ``initdb`` on PATH); tests are skipped when unavailable.
"""

import os

import pytest


def _pg_config():
    """Return ``(conn_params, instance)``; instance is not None only for
    a temporary testing.postgresql server the caller must stop."""
    if 'GNR_TEST_PG_PASSWORD' in os.environ:
        return {
            'host': os.environ.get('GNR_TEST_PG_HOST', '127.0.0.1'),
            'port': os.environ.get('GNR_TEST_PG_PORT', '5432'),
            'user': os.environ.get('GNR_TEST_PG_USER', 'postgres'),
            'password': os.environ.get('GNR_TEST_PG_PASSWORD'),
        }, None
    try:
        from testing.postgresql import Postgresql
    except ImportError:
        pytest.skip("testing.postgresql is not installed")
    # initdb requires a proper LANG
    os.environ.setdefault('LANG', 'en_GB.UTF-8')
    try:
        instance = Postgresql()
    except Exception as error:  # no initdb available, etc.
        pytest.skip(f"cannot start ephemeral PostgreSQL: {error}")
    dsn = instance.dsn()
    return {
        'host': dsn.get('host'),
        'port': str(dsn.get('port')),
        'user': dsn.get('user'),
        'password': dsn.get('password'),
    }, instance


@pytest.fixture(scope='session')
def pg_server():
    """Connection params of the PostgreSQL server used by the DB-bound tests."""
    conf, instance = _pg_config()
    yield conf
    if instance is not None:
        instance.stop()


@pytest.fixture(scope='class')
def migration_env(request, pg_server):
    """Bind a migration test class to a fresh database on the PG server.

    The class must define ``dbname`` and inherit ``BaseMigrationTest``
    (see ``tests/support/migration_base.py``).
    """
    request.cls.setup_env(pg_server)
    yield
    request.cls.teardown_env()
