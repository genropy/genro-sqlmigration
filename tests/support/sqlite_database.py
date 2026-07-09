"""SQLite test database.

A thin :class:`SqliteDatabase` subclass bound to a temporary directory,
plus a ``dropDb`` helper that closes the connection and deletes the
per-schema database files. The tests therefore exercise the production
``SqliteAdapter``/``SqliteDatabase``.
"""

import os

from genro_sqlmigration.adapters import SqliteDatabase


class SqliteTestDatabase(SqliteDatabase):
    """SqliteDatabase bound to a temporary directory."""

    def __init__(self, tmp_dir, application_schemas=None,
                 read_only_schemas=None, tenant_schemas=None):
        dbname = os.path.join(str(tmp_dir), 'base.db')
        super().__init__(
            {'dbname': dbname},
            application_schemas=application_schemas,
            read_only_schemas=read_only_schemas,
            tenant_schemas=tenant_schemas,
        )

    def dropDb(self, dbname=None):
        """Close the connection and delete the main and per-schema files."""
        self.closeConnection()
        paths = [self.get_dbname(), *self.schema_files().values()]
        for path in paths:
            if os.path.exists(path):
                os.remove(path)
