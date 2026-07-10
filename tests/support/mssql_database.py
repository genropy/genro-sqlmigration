"""Microsoft SQL Server test database and container lifecycle.

A thin :class:`MssqlDatabase` subclass binding a live SQL Server 2022
container (started with Docker) and a mutable test model, plus a
drop-database helper. The integration suite therefore exercises the
production ``MssqlAdapter``/``MssqlDatabase``.

The container helper skips (with an explicit reason) when Docker is
unavailable; otherwise it starts an ephemeral container on a free port,
waits for the (slow) SQL Server startup by retry-connecting, and tears
it down with ``docker rm -f``.
"""

import shutil
import socket
import subprocess
import time

from genro_sqlmigration.adapters import MssqlDatabase

SA_PASSWORD = 'GsmTest!Passw0rd'
IMAGE = 'mcr.microsoft.com/mssql/server:2022-latest'
READY_TIMEOUT = 300
READY_POLL = 3


class MssqlTestDatabase(MssqlDatabase):
    """MssqlDatabase bound to a test model and an ephemeral SQL Server."""

    def __init__(self, dbname, mssql_conf, model):
        params = {
            'server': mssql_conf['server'],
            'port': mssql_conf['port'],
            'user': mssql_conf['user'],
            'password': mssql_conf['password'],
            'dbname': dbname,
        }
        super().__init__(params)
        self.model = model

    def getApplicationSchemas(self):
        """Schemas come from the live model, which mutates across tests."""
        return self.model.schema_names()

    def dropDb(self, dbname):
        """Drop the test database, forcing single-user to evict connections."""
        self.closeConnection()
        self._adapter.execute(
            f'IF DB_ID(\'{dbname}\') IS NOT NULL BEGIN '
            f'ALTER DATABASE "{dbname}" SET SINGLE_USER WITH ROLLBACK IMMEDIATE; '
            f'DROP DATABASE "{dbname}"; END',
            manager=True,
        )


class MssqlContainer:
    """Docker-managed SQL Server 2022 instance for the integration suite."""

    def __init__(self):
        self.container_id = None
        self.port = None

    def free_port(self):
        """Reserve a free TCP port by binding to 0 and reading it back."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(('', 0))
            return sock.getsockname()[1]

    def docker_available(self):
        """True if the docker CLI exists and the daemon is reachable."""
        if shutil.which('docker') is None:
            return False
        result = subprocess.run(
            ['docker', 'info'], capture_output=True, text=True
        )
        return result.returncode == 0

    def conf(self):
        """Connection config dict for the running container."""
        return {
            'server': '127.0.0.1',
            'port': str(self.port),
            'user': 'sa',
            'password': SA_PASSWORD,
        }

    def start(self):
        """Start the container and wait until SQL Server accepts connections."""
        self.port = self.free_port()
        result = subprocess.run(
            [
                'docker', 'run', '-d', '--rm',
                '-e', 'ACCEPT_EULA=Y',
                '-e', f'MSSQL_SA_PASSWORD={SA_PASSWORD}',
                '-p', f'{self.port}:1433',
                IMAGE,
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f'docker run failed: {result.stderr.strip()}')
        self.container_id = result.stdout.strip()
        self.wait_ready()

    def wait_ready(self):
        """Retry-connect to ``master`` until ready or the timeout elapses."""
        import pymssql
        deadline = time.monotonic() + READY_TIMEOUT
        last_error = None
        while time.monotonic() < deadline:
            try:
                conn = pymssql.connect(
                    server='127.0.0.1', port=str(self.port),
                    user='sa', password=SA_PASSWORD, database='master',
                )
                conn.close()
                return
            except Exception as error:  # container still booting
                last_error = error
                time.sleep(READY_POLL)
        raise RuntimeError(f'SQL Server did not become ready: {last_error}')

    def stop(self):
        """Force-remove the container."""
        if self.container_id:
            subprocess.run(
                ['docker', 'rm', '-f', self.container_id],
                capture_output=True, text=True,
            )
            self.container_id = None
