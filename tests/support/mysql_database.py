"""MySQL test database and ephemeral container lifecycle.

Provides:

- :class:`MysqlTestDatabase`, a thin :class:`MysqlDatabase` subclass bound
  to the ephemeral container and the mutable test model, so the
  integration suite exercises the production ``MysqlAdapter``/``MysqlDatabase``.
- container helpers: skip when Docker is unavailable, start an ephemeral
  ``mysql:8`` container on a free port, wait for readiness by
  retry-connecting, and tear it down.
"""

import shutil
import socket
import subprocess
import time

import pytest

from genro_sqlmigration.adapters import MysqlDatabase

MYSQL_IMAGE = 'mysql:8'
MYSQL_ROOT_PASSWORD = 'gsmtest'
READINESS_TIMEOUT = 180
READINESS_POLL = 2


class MysqlTestDatabase(MysqlDatabase):
    """MysqlDatabase bound to a test model and an ephemeral MySQL server."""

    def __init__(self, mysql_conf, model):
        params = {
            'host': mysql_conf['host'],
            'port': mysql_conf['port'],
            'user': mysql_conf['user'],
            'password': mysql_conf['password'],
            'dbname': mysql_conf['dbname'],
        }
        super().__init__(params)
        self.model = model

    def getApplicationSchemas(self):
        """Schemas come from the live model, which mutates across tests."""
        return self.model.schema_names()

    def dropDb(self, dbname):
        """Drop a test database (schema) if it exists, on the manager connection.

        Foreign-key checks are disabled around the drop so cross-schema
        FKs left by earlier scenarios do not block teardown.
        """
        self.closeConnection()
        self._adapter.execute(
            'SET FOREIGN_KEY_CHECKS = 0;\n'
            f'DROP DATABASE IF EXISTS "{dbname}";\n'
            'SET FOREIGN_KEY_CHECKS = 1;',
            manager=True,
        )


def _docker_available():
    """Return True if the docker CLI exists and the daemon is reachable."""
    if shutil.which('docker') is None:
        return False
    result = subprocess.run(
        ['docker', 'info'], capture_output=True, text=True
    )
    return result.returncode == 0


def _free_port():
    """Return a free TCP port by binding to port 0 and releasing it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _wait_ready(conf):
    """Poll the server with pymysql until it accepts connections."""
    import pymysql
    deadline = time.monotonic() + READINESS_TIMEOUT
    last_error = None
    while time.monotonic() < deadline:
        try:
            connection = pymysql.connect(
                host=conf['host'], port=conf['port'],
                user=conf['user'], password=conf['password'],
            )
            connection.close()
            return
        except pymysql.err.MySQLError as error:
            last_error = error
            time.sleep(READINESS_POLL)
    raise RuntimeError(f"MySQL container not ready in time: {last_error}")


def start_mysql_container():
    """Start an ephemeral mysql:8 container; return (conf, container_id).

    Skips the calling test module when Docker is unavailable.
    """
    if not _docker_available():
        pytest.skip("Docker is not available (CLI missing or daemon unreachable)")
    port = _free_port()
    result = subprocess.run(
        ['docker', 'run', '-d', '--rm',
         '-e', f'MYSQL_ROOT_PASSWORD={MYSQL_ROOT_PASSWORD}',
         '-p', f'{port}:3306', MYSQL_IMAGE],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"cannot start mysql:8 container: {result.stderr.strip()}")
    container_id = result.stdout.strip()
    conf = {
        'host': '127.0.0.1',
        'port': port,
        'user': 'root',
        'password': MYSQL_ROOT_PASSWORD,
    }
    try:
        _wait_ready(conf)
    except Exception:
        stop_mysql_container(container_id)
        raise
    return conf, container_id


def stop_mysql_container(container_id):
    """Force-remove the ephemeral container."""
    subprocess.run(
        ['docker', 'rm', '-f', container_id],
        capture_output=True, text=True,
    )
