"""PostgreSQL test database.

A thin :class:`PgDatabase` subclass binding the ephemeral test server and
the mutable test model, plus the drop-database lifecycle helper. The
oracle suite therefore exercises the production ``PgAdapter``/``PgDatabase``.
"""

from genro_sqlmigration.adapters import PgDatabase


class PgTestDatabase(PgDatabase):
    """PgDatabase bound to a test model and an ephemeral PostgreSQL server."""

    def __init__(self, dbname, pg_conf, model):
        params = {
            'host': pg_conf.get('host'),
            'port': pg_conf.get('port'),
            'user': pg_conf.get('user'),
            'password': pg_conf.get('password'),
            'dbname': dbname,
        }
        params = {k: v for k, v in params.items() if v is not None}
        super().__init__(params)
        self.model = model

    def getApplicationSchemas(self):
        """Schemas come from the live model, which mutates across tests."""
        return self.model.schema_names()

    def dropDb(self, dbname):
        """Drop the test database, terminating any leftover connections.

        Statements run one by one: PostgreSQL executes a multi-statement
        message in an implicit transaction, where DROP DATABASE is refused.
        """
        self.closeConnection()
        self._adapter.execute(
            f"""SELECT pg_terminate_backend(pid) FROM pg_stat_activity
                WHERE datname = '{dbname}' AND pid <> pg_backend_pid()""",
            manager=True,
        )
        self._adapter.execute(f'DROP DATABASE IF EXISTS "{dbname}"', manager=True)
