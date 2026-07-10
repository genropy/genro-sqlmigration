# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""
mssql_adapter.py - Concrete Microsoft SQL Server adapter/database pair
=======================================================================

``MssqlAdapter`` wires DDL generation to :class:`MssqlWriter` and
introspection to :class:`MssqlReader`; ``MssqlDatabase`` is the
:class:`Database` facade consumers instantiate. pymssql is required at
connection time only (optional ``mssql`` extra).

``execute`` splits the incoming SQL script into individual statements and
runs them one by one on a single connection: this satisfies the T-SQL
rule that ``CREATE SCHEMA`` must be alone in its batch, and mirrors the
legacy per-statement autocommit stance.
"""

from genro_sqlmigration.database import BaseAdapter, Database
from genro_sqlmigration.exceptions import (
    NonExistingDbException,
    SqlConnectionException,
    SqlMigrationError,
)
from genro_sqlmigration.readers import MssqlReader
from genro_sqlmigration.writers import MssqlWriter


class MssqlAdapter(BaseAdapter):
    """SQL Server adapter: MssqlWriter for DDL, MssqlReader for introspection, pymssql for execution."""

    TYPE_CONVERSIONS = MssqlWriter.TYPE_CONVERSIONS

    def __init__(self, database):
        self.database = database
        self.writer = MssqlWriter()
        self._reader = None

    # -- Connections ---------------------------------------------------------

    def connect(self, manager=False, autocommit=False):
        """Open a pymssql connection with the #655 error taxonomy.

        A missing database (message contains "Cannot open database") raises
        :class:`NonExistingDbException`; any other failure raises
        :class:`SqlConnectionException`. ``SET QUOTED_IDENTIFIER ON`` is
        issued so double-quoted identifiers parse consistently.
        """
        import pymssql  # optional dependency (mssql extra)
        params = dict(self.database.connection_params(manager=manager))
        dbname = params.pop('dbname', None)
        if dbname is not None:
            params['database'] = dbname
        try:
            connection = pymssql.connect(autocommit=autocommit, **params)
        except pymssql.OperationalError as error:
            if 'cannot open database' in str(error).lower():
                raise NonExistingDbException(self.database.get_dbname()) from error
            raise SqlConnectionException(
                self.database.get_dbname(), original_error=error
            ) from error
        with connection.cursor() as cursor:
            cursor.execute('SET QUOTED_IDENTIFIER ON')
        return connection

    def split_statements(self, sql):
        """Split a SQL script into individual statements.

        Statements are separated by a ``;`` at end of line; empty fragments
        are ignored. T-SQL cannot batch several DDL statements (e.g.
        ``CREATE SCHEMA``) together, so each is executed on its own.
        """
        statements = []
        for chunk in sql.split(';\n'):
            statement = chunk.strip().rstrip(';').strip()
            if statement:
                statements.append(statement)
        return statements

    def execute(self, sql, autoCommit=False, manager=False):
        """Run a (possibly multi-statement) SQL script on a fresh connection.

        The script is split into individual statements executed one by one;
        manager connections use autocommit (CREATE DATABASE cannot run in a
        transaction).
        """
        autocommit = autoCommit or manager
        connection = self.connect(manager=manager, autocommit=autocommit)
        try:
            with connection.cursor() as cursor:
                for statement in self.split_statements(sql):
                    cursor.execute(statement)
            if not autocommit:
                connection.commit()
        finally:
            connection.close()

    # -- Introspection (delegated to MssqlReader) -----------------------------

    @property
    def reader(self):
        """Lazily build the MssqlReader bound to this database's connection params."""
        if self._reader is None:
            self._reader = MssqlReader(self.database.connection_params())
        return self._reader

    @property
    def capabilities(self):
        """Dialect capability set, declared by the writer."""
        return self.writer.CAPABILITIES

    # -- SQL generation (delegated to MssqlWriter) ----------------------------

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


class MssqlDatabase(Database):
    """Production Microsoft SQL Server database facade.

    Consumers pass pymssql connection kwargs (``dbname`` required) and the
    schema lists; the migrator reads schemas through the getter methods.
    """

    def __init__(self, connection_params, application_schemas=None,
                 read_only_schemas=None, tenant_schemas=None):
        self._connection_params = dict(connection_params)
        if 'dbname' not in self._connection_params:
            raise SqlMigrationError("connection_params must include 'dbname'")
        self._application_schemas = list(application_schemas or [])
        self._read_only_schemas = list(read_only_schemas or [])
        self._tenant_schemas = list(tenant_schemas or [])
        self._adapter = MssqlAdapter(self)
        self._connection = None

    @property
    def adapter(self):
        return self._adapter

    def get_dbname(self):
        return self._connection_params['dbname']

    def connection_params(self, manager=False):
        """pymssql connection kwargs; ``manager`` targets the ``master`` DB."""
        params = dict(self._connection_params)
        if manager:
            params['dbname'] = 'master'
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
        if self._connection is None:
            self._connection = self._adapter.connect()

    def execute(self, sql):
        self.startup()
        cursor = self._connection.cursor()
        cursor.execute(sql)
        return cursor

    def commit(self):
        if self._connection is not None:
            self._connection.commit()

    def closeConnection(self):
        if self._connection is not None:
            self._connection.close()
        self._connection = None
