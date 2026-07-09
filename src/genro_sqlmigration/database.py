"""
database.py - Database abstraction layer
==========================================

Provides the ``Database`` and ``BaseAdapter`` classes that replicate
the ``self.db`` / ``self.db.adapter`` interface used by the migration
modules (command_builder, executor, db_extractor, migrator).

In Genropy these roles are filled by ``GnrSqlDb`` and the database
adapter (e.g. ``GnrPostgresAdapter``).  Here they are standalone
classes that delegate SQL generation to a :class:`BaseWriter` and
SQL execution / introspection to a :class:`BaseReader`.
"""

from abc import ABC, abstractmethod


class BaseAdapter(ABC):
    """Abstract adapter exposing the interface consumed by the migration modules.

    Subclasses (e.g. ``PgAdapter``) wire each method to the appropriate
    reader or writer implementation.

    Attributes:
        TYPE_CONVERSIONS: dict mapping ``(old_dtype, new_dtype)`` to
            conversion rules (None, True, or a USING expression string).
    """

    TYPE_CONVERSIONS = {}

    # -- SQL generation (delegated to writer) --------------------------------

    @abstractmethod
    def createDbSql(self, dbname, encoding='UNICODE'):
        ...

    @abstractmethod
    def createSchemaSql(self, schema_name):
        ...

    @abstractmethod
    def columnSqlType(self, dtype, size=None):
        ...

    @abstractmethod
    def columnSqlDefinition(self, column_name, dtype, size=None,
                            notnull=False, default=None,
                            extra_sql=None, generated_expression=None):
        ...

    @abstractmethod
    def adaptSqlName(self, name):
        ...

    @abstractmethod
    def struct_constraint_sql(self, constraint_name, constraint_type,
                              columns=None, check_clause=None, **kwargs):
        ...

    @abstractmethod
    def struct_foreign_key_sql(self, fk_name, columns, related_table,
                               related_schema, related_columns,
                               on_delete=None, on_update=None,
                               deferrable=False, initially_deferred=False):
        ...

    @abstractmethod
    def struct_create_index_sql(self, schema_name, table_name, columns,
                                index_name=None, unique=False, method=None,
                                with_options=None, tablespace=None,
                                where=None):
        ...

    @abstractmethod
    def struct_create_extension_sql(self, extension_name):
        ...

    @abstractmethod
    def struct_comment_on_column_sql(self, schema_name, table_name,
                                     column_name, comment):
        ...

    @abstractmethod
    def struct_comment_on_table_sql(self, schema_name, table_name, comment):
        ...

    @abstractmethod
    def struct_drop_table_pkey_sql(self, schema_name, table_name):
        ...

    @abstractmethod
    def struct_add_table_pkey_sql(self, schema_name, table_name, pkeys):
        ...

    @abstractmethod
    def struct_alter_column_sql(self, column_name, new_sql_type, **kwargs):
        ...

    @abstractmethod
    def struct_alter_column_with_conversion_sql(self, column_name,
                                                 new_sql_type,
                                                 conversion_expression,
                                                 **kwargs):
        ...

    @abstractmethod
    def struct_add_not_null_sql(self, column_name, **kwargs):
        ...

    @abstractmethod
    def struct_drop_not_null_sql(self, column_name, **kwargs):
        ...

    @abstractmethod
    def struct_drop_constraint_sql(self, constraint_name, **kwargs):
        ...

    def struct_is_empty_column(self, schema_name, table_name, column_name):
        return self.reader.is_empty_column(schema_name, table_name, column_name)

    @property
    @abstractmethod
    def reader(self):
        """Return the introspection reader (:class:`BaseReader`) for this dialect."""
        ...

    # -- Execution (delegated to reader/connection) --------------------------

    @abstractmethod
    def execute(self, sql, autoCommit=False, manager=False):
        ...

    @abstractmethod
    def connect(self):
        ...


class Database(ABC):
    """Abstract database object exposing the ``self.db`` interface.

    Provides access to the adapter and to database-level queries
    used by the migrator and db_extractor.
    """

    @property
    @abstractmethod
    def adapter(self):
        """Return the database adapter instance."""
        ...

    @abstractmethod
    def get_dbname(self):
        ...

    @abstractmethod
    def execute(self, sql):
        ...

    @abstractmethod
    def getApplicationSchemas(self):
        ...

    @abstractmethod
    def readOnlySchemas(self):
        ...

    @abstractmethod
    def getTenantSchemas(self):
        ...
