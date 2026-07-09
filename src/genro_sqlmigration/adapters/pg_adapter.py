# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""
pg_adapter.py - Concrete PostgreSQL adapter/database pair
==========================================================

``PgAdapter`` wires DDL generation to :class:`PgWriter` and introspection
to :class:`PgReader`; ``PgDatabase`` is the :class:`Database` facade
consumers instantiate. psycopg 3 is required at connection time only
(optional ``postgresql`` extra).
"""

from genro_sqlmigration.database import BaseAdapter, Database
from genro_sqlmigration.exceptions import (
    NonExistingDbException,
    SqlConnectionException,
    SqlMigrationError,
)
from genro_sqlmigration.readers import PgReader
from genro_sqlmigration.writers import PgWriter


class PgAdapter(BaseAdapter):
    """PostgreSQL adapter: PgWriter for DDL, PgReader for introspection, psycopg 3 for execution."""

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
        import psycopg  # optional dependency (postgresql extra)
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

    @property
    def capabilities(self):
        """Dialect capability set, declared by the writer."""
        return self.writer.CAPABILITIES

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

    def struct_add_column_sql(self, column_definition):
        return self.writer.add_column_sql(column_definition)

    def struct_alter_table_commands(self, schema_name, table_name,
                                    column_fragments):
        return self.writer.alter_table_commands(
            schema_name, table_name, column_fragments
        )


class PgDatabase(Database):
    """Production PostgreSQL database facade.

    Consumers pass psycopg connection kwargs and the schema lists; the
    migrator reads schemas through the getter methods.
    """

    def __init__(self, connection_params, application_schemas=None,
                 read_only_schemas=None, tenant_schemas=None):
        self._connection_params = dict(connection_params)
        if 'dbname' not in self._connection_params:
            raise SqlMigrationError("connection_params must include 'dbname'")
        self._application_schemas = list(application_schemas or [])
        self._read_only_schemas = list(read_only_schemas or [])
        self._tenant_schemas = list(tenant_schemas or [])
        self._adapter = PgAdapter(self)
        self._connection = None

    @property
    def adapter(self):
        return self._adapter

    def get_dbname(self):
        return self._connection_params['dbname']

    def connection_params(self, manager=False):
        """psycopg connection kwargs; ``manager`` targets the maintenance DB."""
        params = dict(self._connection_params)
        if manager:
            params['dbname'] = 'postgres'
        return params

    # -- Schemas seen by the migrator -----------------------------------------

    def getApplicationSchemas(self):
        return self._application_schemas

    def readOnlySchemas(self):
        return self._read_only_schemas

    def getTenantSchemas(self):
        return self._tenant_schemas

    # -- Persistent connection ------------------------------------------------

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
