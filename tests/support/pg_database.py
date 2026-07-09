"""Concrete PostgreSQL ``Database``/``BaseAdapter`` pair for the test suite.

The package ships the abstract facade (``database.py``) plus the dialect
building blocks (``PgWriter`` for DDL, ``PgReader`` for introspection).
This module wires them to a real PostgreSQL via psycopg 3 and is the first
real consumer of the facade — the reference implementation for the producer
guide (roadmap doc ``04``).

Introspection is delegated to the package's ``PgReader`` via the ``reader``
property; the adapter keeps execution (``connect``/``execute``) plus DDL
delegation to ``PgWriter``.
"""

import psycopg

from genro_sqlmigration.database import BaseAdapter, Database
from genro_sqlmigration.exceptions import (
    NonExistingDbException,
    SqlConnectionException,
)
from genro_sqlmigration.readers import PgReader
from genro_sqlmigration.writers import PgWriter


class PgTestAdapter(BaseAdapter):
    """Adapter for PostgreSQL: PgWriter for DDL, psycopg 3 for execution."""

    TYPE_CONVERSIONS = PgWriter.TYPE_CONVERSIONS

    def __init__(self, database):
        self.database = database
        self.writer = PgWriter()
        self._reader = None

    # -- Connections ---------------------------------------------------------

    def connect(self, manager=False, autocommit=False):
        """Open a psycopg connection with the #655 error taxonomy.

        A missing database raises :class:`NonExistingDbException`; any
        other connection failure raises :class:`SqlConnectionException`
        so it is never mistaken for "database to be created".
        """
        params = self.database.connection_params(manager=manager)
        try:
            return psycopg.connect(**params, autocommit=autocommit)
        except psycopg.OperationalError as error:
            if 'does not exist' in str(error).lower():
                raise NonExistingDbException(self.database.get_dbname()) from error
            raise SqlConnectionException(
                self.database.get_dbname(), original_error=error
            ) from error

    def execute(self, sql, autoCommit=False, manager=False):
        """Run a (possibly multi-statement) SQL string on a fresh connection."""
        connection = self.connect(manager=manager, autocommit=autoCommit or manager)
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql)
            if not connection.autocommit:
                connection.commit()
        finally:
            connection.close()

    # -- Introspection (delegated to PgReader) --------------------------------

    @property
    def reader(self):
        """Lazily build the PgReader bound to this database's connection params."""
        if self._reader is None:
            self._reader = PgReader(self.database.connection_params())
        return self._reader

    # -- SQL generation (delegated to PgWriter) -------------------------------

    def createDbSql(self, dbname, encoding='UNICODE'):
        return self.writer.create_db_sql(dbname, encoding=encoding)

    def createSchemaSql(self, schema_name):
        return self.writer.create_schema_sql(schema_name)

    def columnSqlType(self, dtype, size=None):
        return self.writer.column_sql_type(dtype, size=size)

    def columnSqlDefinition(self, column_name, dtype, size=None,
                            notnull=False, default=None,
                            extra_sql=None, generated_expression=None):
        return self.writer.column_sql_definition(
            column_name, dtype, size=size, notnull=notnull, default=default,
            extra_sql=extra_sql, generated_expression=generated_expression
        )

    def adaptSqlName(self, name):
        return f'"{name}"'

    def struct_constraint_sql(self, constraint_name, constraint_type,
                              columns=None, check_clause=None, **kwargs):
        return self.writer.constraint_sql(
            constraint_name, constraint_type,
            columns=columns, check_clause=check_clause
        )

    def struct_foreign_key_sql(self, fk_name, columns, related_table,
                               related_schema, related_columns,
                               on_delete=None, on_update=None,
                               deferrable=False, initially_deferred=False):
        return self.writer.foreign_key_sql(
            fk_name, columns, related_table, related_schema, related_columns,
            on_delete=on_delete, on_update=on_update,
            deferrable=deferrable, initially_deferred=initially_deferred
        )

    def struct_create_index_sql(self, schema_name, table_name, columns,
                                index_name=None, unique=False, method=None,
                                with_options=None, tablespace=None,
                                where=None):
        return self.writer.create_index_sql(
            schema_name, table_name, columns,
            index_name=index_name, unique=unique, method=method,
            with_options=with_options, tablespace=tablespace, where=where
        )

    def struct_create_extension_sql(self, extension_name):
        return self.writer.create_extension_sql(extension_name)

    def struct_comment_on_column_sql(self, schema_name, table_name,
                                     column_name, comment):
        return self.writer.comment_on_column_sql(
            schema_name, table_name, column_name, comment
        )

    def struct_comment_on_table_sql(self, schema_name, table_name, comment):
        return self.writer.comment_on_table_sql(schema_name, table_name, comment)

    def struct_drop_table_pkey_sql(self, schema_name, table_name):
        return self.writer.drop_table_pkey_sql(schema_name, table_name)

    def struct_add_table_pkey_sql(self, schema_name, table_name, pkeys):
        return self.writer.add_table_pkey_sql(schema_name, table_name, pkeys)

    def struct_alter_column_sql(self, column_name, new_sql_type, **kwargs):
        return self.writer.alter_column_sql(column_name, new_sql_type)

    def struct_alter_column_with_conversion_sql(self, column_name,
                                                new_sql_type,
                                                conversion_expression,
                                                **kwargs):
        return self.writer.alter_column_with_conversion_sql(
            column_name, new_sql_type, conversion_expression
        )

    def struct_add_not_null_sql(self, column_name, **kwargs):
        return self.writer.add_not_null_sql(column_name)

    def struct_drop_not_null_sql(self, column_name, **kwargs):
        return self.writer.drop_not_null_sql(column_name)

    def struct_drop_constraint_sql(self, constraint_name, **kwargs):
        return self.writer.drop_constraint_sql(constraint_name)


class PgTestDatabase(Database):
    """Database facade bound to a test model and a PostgreSQL server."""

    def __init__(self, dbname, pg_conf, model):
        self._dbname = dbname
        self.pg_conf = pg_conf
        self.model = model
        self._adapter = PgTestAdapter(self)
        self._connection = None

    @property
    def adapter(self):
        return self._adapter

    def get_dbname(self):
        return self._dbname

    def connection_params(self, manager=False):
        """psycopg connection kwargs; ``manager`` targets the maintenance DB."""
        params = {
            'host': self.pg_conf.get('host'),
            'port': self.pg_conf.get('port'),
            'user': self.pg_conf.get('user'),
            'password': self.pg_conf.get('password'),
            'dbname': 'postgres' if manager else self._dbname,
        }
        return {k: v for k, v in params.items() if v is not None}

    # -- Persistent connection for test DML/queries ---------------------------

    def startup(self):
        """Open the persistent connection lazily (legacy ``db.startup()``)."""
        if self._connection is None or self._connection.closed:
            self._connection = self._adapter.connect()

    def execute(self, sql):
        self.startup()
        cursor = self._connection.cursor()
        cursor.execute(sql)
        return cursor

    def commit(self):
        if self._connection and not self._connection.closed:
            self._connection.commit()

    def closeConnection(self):
        if self._connection and not self._connection.closed:
            self._connection.close()
        self._connection = None

    # -- Schemas seen by the migrator -----------------------------------------

    def getApplicationSchemas(self):
        return self.model.schema_names()

    def readOnlySchemas(self):
        return []

    def getTenantSchemas(self):
        return []

    # -- Test lifecycle helpers ------------------------------------------------

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
