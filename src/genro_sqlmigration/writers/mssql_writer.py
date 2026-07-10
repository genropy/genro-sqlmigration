# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""
mssql_writer.py - SQL generator for Microsoft SQL Server (T-SQL)
=================================================================

Generates T-SQL fragments and commands for SQL Server. Mirrors
:class:`PgWriter` in shape and style, adapted to Transact-SQL rules:

- ``ADD`` without the ``COLUMN`` keyword (:meth:`add_column_sql`);
- one ``ALTER TABLE`` per fragment, because T-SQL cannot mix ``ADD`` and
  ``ALTER COLUMN`` in one statement (:meth:`alter_table_commands`);
- ``ALTER COLUMN "col" <type>`` requires the full type, so NOT NULL
  toggles and USING-style conversions are not expressible and raise.

The dialect declares a reduced capability set: comments, extensions,
event triggers, deferrable FKs and index method/tablespace/with-options
are absent, so the shared migrator strips them before the diff.
"""

from genro_sqlmigration.exceptions import SqlMigrationError
from genro_sqlmigration.writers.base_writer import BaseWriter

# Mapping normalized dtype -> T-SQL type. National (Unicode) string types
# are used for text (nvarchar/nchar) so the default collation is enough.
REV_TYPES_DICT = {
    'A': 'nvarchar',
    'B': 'bit',
    'C': 'nchar',
    'D': 'date',
    'DH': 'datetime2',
    'DHZ': 'datetimeoffset',
    'H': 'time',
    'I': 'int',
    'L': 'bigint',
    'N': 'numeric',
    'O': 'varbinary(max)',
    'R': 'float',
    'T': 'nvarchar(max)',
    'jsonb': 'nvarchar(max)',
    'serial': 'int IDENTITY(1,1)',
}


class MssqlWriter(BaseWriter):
    """SQL generation writer for Microsoft SQL Server.

    Generates fragments conforming to Transact-SQL syntax.

    Args:
        connection_params: pymssql connection kwargs for direct execution.
            If None, the writer only generates SQL without executing it.
    """

    # No column-type conversions: T-SQL ALTER COLUMN needs the full type and
    # has no USING clause, so conversions go through drop/add at a higher level.
    TYPE_CONVERSIONS = {}

    # SQL Server supports FKs, table constraints, filtered indexes and the
    # DDL operations; it lacks the representation features stripped by the
    # shared migrator (see BaseWriter.CAPABILITIES).
    CAPABILITIES = frozenset({
        'foreign_keys', 'table_constraints', 'index_where',
        'alter_column_type', 'drop_constraint', 'add_constraint',
    })

    def __init__(self, connection_params=None):
        self.connection_params = connection_params

    def add_column_sql(self, column_definition):
        """T-SQL ADD (no COLUMN keyword)."""
        return f'ADD {column_definition}'

    def alter_table_commands(self, schema_name, table_name, column_fragments):
        """One ALTER TABLE per fragment.

        T-SQL cannot mix ADD and ALTER COLUMN in a single statement, so each
        fragment becomes its own ALTER TABLE.
        """
        full_table = self.table_fullname(schema_name, table_name)
        return [f'ALTER TABLE {full_table}\n{fragment};' for fragment in column_fragments]

    def column_sql_type(self, dtype, size=None):
        """Return the T-SQL type for a dtype and optional size.

        Args:
            dtype: Normalized dtype (e.g. 'T', 'I', 'N', 'C').
            size: Column size (optional).

        Returns:
            str: T-SQL type (e.g. 'nvarchar(100)', 'int', 'numeric(10,2)').
        """
        if dtype != 'N' and size:
            if ':' in size:
                size = size.split(':')[1]
                dtype = 'A'
            else:
                dtype = 'C'
        if size:
            return f'{REV_TYPES_DICT[dtype]}({size})'
        return REV_TYPES_DICT[dtype]

    def column_sql_definition(self, column_name, dtype, size=None,
                              notnull=False, default=None,
                              extra_sql=None, generated_expression=None):
        """Generate the full T-SQL definition of a column.

        Args:
            column_name: Column name.
            dtype: Normalized dtype.
            size: Column size.
            notnull: If truthy, appends NOT NULL.
            default: SQL default value.
            extra_sql: Extra SQL to append.
            generated_expression: Expression for computed columns.

        Returns:
            str: Column definition (e.g. ``"name" nvarchar(100) NOT NULL``).
        """
        sql_type = self.column_sql_type(dtype, size)
        parts = [f'"{column_name}" {sql_type}']

        if generated_expression:
            parts.append(f'AS ({generated_expression})')
        else:
            if default:
                parts.append(f'DEFAULT {default}')
            if notnull:
                parts.append('NOT NULL')
        if extra_sql:
            parts.append(extra_sql)

        return ' '.join(parts)

    def table_fullname(self, schema_name, table_name):
        """Return the quoted qualified table name (``"schema"."table"``)."""
        return f'"{schema_name}"."{table_name}"'

    def create_db_sql(self, dbname, encoding='UNICODE'):
        """Generate CREATE DATABASE (encoding ignored: default collation)."""
        return f'CREATE DATABASE "{dbname}";'

    def create_schema_sql(self, schema_name):
        """Generate CREATE SCHEMA.

        T-SQL requires CREATE SCHEMA alone in its batch; the adapter's
        statement-splitting execute guarantees the isolation.
        """
        return f'CREATE SCHEMA "{schema_name}";'

    def alter_column_sql(self, column_name, new_sql_type):
        """Generate ALTER COLUMN with the full type (no USING)."""
        return f'ALTER COLUMN "{column_name}" {new_sql_type}'

    def alter_column_with_conversion_sql(self, column_name, new_sql_type,
                                         conversion_expression):
        """T-SQL has no ALTER COLUMN ... USING; conversions are unsupported."""
        raise SqlMigrationError(
            'MSSQL does not support ALTER COLUMN with a conversion expression'
        )

    def add_not_null_sql(self, column_name):
        """NOT NULL toggle needs the full type in T-SQL ALTER COLUMN: unsupported.

        Setting NOT NULL requires ``ALTER COLUMN "col" <type> NOT NULL`` (the
        type cannot be omitted), which this method does not receive; tests
        avoid notnull changes.
        """
        raise SqlMigrationError(
            'MSSQL ALTER COLUMN requires the full type; NOT NULL toggle is '
            'not expressible here'
        )

    def drop_not_null_sql(self, column_name):
        """Dropping NOT NULL needs the full type in T-SQL ALTER COLUMN: unsupported."""
        raise SqlMigrationError(
            'MSSQL ALTER COLUMN requires the full type; NOT NULL toggle is '
            'not expressible here'
        )

    def constraint_sql(self, constraint_name, constraint_type, columns=None,
                       check_clause=None):
        """Generate a CONSTRAINT definition (UNIQUE or CHECK).

        Args:
            constraint_name: Constraint name.
            constraint_type: "UNIQUE" or "CHECK".
            columns: Column list (for UNIQUE).
            check_clause: Clause (for CHECK).

        Returns:
            str: SQL constraint definition.
        """
        if constraint_type == "UNIQUE":
            columns_str = ', '.join(f'"{col}"' for col in columns)
            return f'CONSTRAINT "{constraint_name}" UNIQUE ({columns_str})'
        elif constraint_type == "CHECK":
            return f'CONSTRAINT "{constraint_name}" CHECK ({check_clause})'
        raise ValueError(f"Unsupported constraint type: {constraint_type}")

    def drop_constraint_sql(self, constraint_name):
        """Generate DROP CONSTRAINT."""
        return f'DROP CONSTRAINT "{constraint_name}"'

    def foreign_key_sql(self, fk_name, columns, related_table, related_schema,
                        related_columns, on_delete=None, on_update=None,
                        deferrable=False, initially_deferred=False):
        """Generate a FOREIGN KEY definition.

        RESTRICT is mapped to NO ACTION (T-SQL has no RESTRICT); the
        deferrable arguments are ignored (T-SQL FKs are never deferrable).

        Args:
            fk_name: FK constraint name.
            columns: Source columns.
            related_table: Target table.
            related_schema: Target schema.
            related_columns: Target columns.
            on_delete: ON DELETE action.
            on_update: ON UPDATE action.
            deferrable: Ignored.
            initially_deferred: Ignored.

        Returns:
            str: Full FK definition.
        """
        columns_str = ', '.join(f'"{col}"' for col in columns)
        related_columns_str = ', '.join(f'"{col}"' for col in related_columns)
        on_delete_str = f" ON DELETE {self._fk_action(on_delete)}" if on_delete else ""
        on_update_str = f" ON UPDATE {self._fk_action(on_update)}" if on_update else ""
        return (
            f'CONSTRAINT "{fk_name}" FOREIGN KEY ({columns_str}) '
            f'REFERENCES "{related_schema}"."{related_table}" '
            f'({related_columns_str})'
            f'{on_delete_str}{on_update_str}'
        )

    def _fk_action(self, action):
        """Map a referential action to T-SQL (RESTRICT has no equivalent)."""
        if action and action.upper() == 'RESTRICT':
            return 'NO ACTION'
        return action

    def create_index_sql(self, schema_name, table_name, columns,
                         index_name=None, unique=False, method=None,
                         with_options=None, tablespace=None, where=None):
        """Generate CREATE INDEX.

        method/tablespace/with_options arrive stripped by the migrator and
        are ignored; ``where`` produces a filtered index.

        Args:
            schema_name: Schema name.
            table_name: Table name.
            columns: Dict {column: sort_order} or list of columns.
            index_name: Index name.
            unique: If True, UNIQUE index.
            method: Ignored.
            with_options: Ignored.
            tablespace: Ignored.
            where: Filter condition (filtered index).

        Returns:
            str: CREATE INDEX command.
        """
        if isinstance(columns, dict):
            column_defs = []
            for column, order in columns.items():
                if order:
                    column_defs.append(f'"{column}" {order}')
                else:
                    column_defs.append(f'"{column}"')
            column_list = ", ".join(column_defs)
        else:
            column_list = ", ".join(f'"{col}"' for col in columns)

        where_clause = f"WHERE {where}" if where else ""
        full_table_name = self.table_fullname(schema_name, table_name)
        unique_clause = ' UNIQUE ' if unique else " "

        sql = (
            f"CREATE{unique_clause}INDEX {index_name} "
            f"ON {full_table_name} "
            f"({column_list}) "
            f"{where_clause}"
        )
        return f'{" ".join(sql.split())};'

    def drop_table_pkey_sql(self, schema_name, table_name):
        """Generate DROP of the PRIMARY KEY constraint.

        The constraint name follows the reader's convention
        ``<table>_pkey`` so drop/add round-trips are stable.
        """
        full_table = self.table_fullname(schema_name, table_name)
        return f'ALTER TABLE {full_table} DROP CONSTRAINT "{table_name}_pkey";'

    def add_table_pkey_sql(self, schema_name, table_name, pkeys):
        """Generate ADD PRIMARY KEY."""
        full_table = self.table_fullname(schema_name, table_name)
        columns = ', '.join(f'"{col.strip()}"' for col in pkeys.split(','))
        return (
            f'ALTER TABLE {full_table} ADD CONSTRAINT "{table_name}_pkey" '
            f'PRIMARY KEY ({columns});'
        )

    def comment_on_column_sql(self, schema_name, table_name, column_name,
                              comment):
        """Comments are stripped by the migrator; never reached for MSSQL."""
        raise SqlMigrationError('MSSQL dialect does not support column comments')

    def comment_on_table_sql(self, schema_name, table_name, comment):
        """Comments are stripped by the migrator; never reached for MSSQL."""
        raise SqlMigrationError('MSSQL dialect does not support table comments')

    def create_extension_sql(self, extension_name):
        """Extensions are stripped by the migrator; never reached for MSSQL."""
        raise SqlMigrationError('MSSQL dialect does not support extensions')

    def execute(self, sql, auto_commit=False, manager=False):
        """Execution belongs to the adapter, not the writer."""
        raise SqlMigrationError('MssqlWriter does not execute SQL; use the adapter')
