# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""
mysql_writer.py - SQL generator for MySQL
==========================================

Generates SQL fragments and commands specific to MySQL 8.0.19+.

All identifiers are double-quoted; MySQL accepts ANSI double quotes only
when the session runs in ``ANSI_QUOTES`` mode, which the adapter and the
reader enable on every connection. SCHEMA and DATABASE are synonyms in
MySQL, so ``CREATE SCHEMA`` behaves like ``CREATE DATABASE``.

dtype -> MySQL type map
-----------------------

::

    A -> varchar(N)   C -> char(N)   T -> text        I -> int
    L -> bigint       R -> double    N -> decimal(P,S) B -> boolean
    D -> date         H -> time      DH -> datetime    DHZ -> timestamp
    O -> blob         serial -> bigint auto_increment  jsonb -> json

Known limitations (v1)
----------------------

- ``alter_column_sql`` emits ``MODIFY COLUMN "col" <type>``. MySQL's
  MODIFY requires the full column definition, so a MODIFY that only
  changes the type silently drops NOT NULL/DEFAULT. v1 accepts this and
  the integration tests only resize nullable, default-less columns.
- ``add_not_null_sql``/``drop_not_null_sql`` are not expressible without
  the full definition and raise :class:`SqlMigrationError`; tests avoid
  NOT NULL changes.
- ``alter_column_with_conversion_sql`` raises: MySQL has no USING clause
  and ``TYPE_CONVERSIONS`` is empty.
"""

from genro_sqlmigration.exceptions import SqlMigrationError
from genro_sqlmigration.writers.base_writer import BaseWriter

# Mapping normalized dtype -> MySQL SQL type.
REV_TYPES_DICT = {
    'A': 'varchar',
    'B': 'boolean',
    'C': 'char',
    'D': 'date',
    'DH': 'datetime',
    'DHZ': 'timestamp',
    'H': 'time',
    'I': 'int',
    'L': 'bigint',
    'N': 'decimal',
    'O': 'blob',
    'R': 'double',
    'T': 'text',
    'jsonb': 'json',
    'serial': 'bigint auto_increment',
}


class MysqlWriter(BaseWriter):
    """SQL generation writer for MySQL.

    Generates SQL fragments compliant with MySQL 8.0.19+ syntax (double
    quotes assume the session runs in ``ANSI_QUOTES`` mode).

    Args:
        connection_params: pymysql connection kwargs (unused by the
            writer, kept for symmetry with PgWriter).
    """

    # MySQL has no USING clause and no simple type conversions.
    TYPE_CONVERSIONS = {}

    # MySQL 8 supports foreign keys, table constraints and the three DDL
    # operations; everything else in the vocabulary is stripped/gated by
    # the shared code (see BaseWriter.CAPABILITIES).
    CAPABILITIES = frozenset({
        'foreign_keys', 'table_constraints',
        'alter_column_type', 'drop_constraint', 'add_constraint',
    })

    def __init__(self, connection_params=None):
        self.connection_params = connection_params

    def column_sql_type(self, dtype, size=None):
        """Return the MySQL SQL type for a dtype.

        Args:
            dtype: Normalized dtype.
            size: Column size.

        Returns:
            str: MySQL SQL type (e.g. ``varchar(100)``, ``decimal(10,2)``).
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
        """Generate the full SQL definition of a MySQL column.

        Args:
            column_name: Column name.
            dtype: Normalized dtype.
            size: Column size.
            notnull: If truthy, adds NOT NULL.
            default: SQL default value.
            extra_sql: Extra SQL to append.
            generated_expression: Expression for GENERATED ALWAYS columns.

        Returns:
            str: Column SQL definition (``"name" varchar(100) NOT NULL``).
        """
        sql_type = self.column_sql_type(dtype, size)
        parts = [f'"{column_name}" {sql_type}']

        if generated_expression:
            parts.append(f'GENERATED ALWAYS AS ({generated_expression}) STORED')
        else:
            if default:
                parts.append(f'DEFAULT {default}')
            if notnull:
                parts.append('NOT NULL')
        if extra_sql:
            parts.append(extra_sql)

        return ' '.join(parts)

    def table_fullname(self, schema_name, table_name):
        """Return the quoted qualified table name (valid in ANSI mode).

        Args:
            schema_name: Schema name.
            table_name: Table name.

        Returns:
            str: ``"schema"."table"``
        """
        return f'"{schema_name}"."{table_name}"'

    def create_db_sql(self, dbname, encoding='UNICODE'):
        """Generate CREATE DATABASE with a utf8mb4 character set.

        The ``encoding`` argument is ignored: MySQL uses named character
        sets (not PostgreSQL-style encodings) and utf8mb4 is the only
        sensible full-Unicode choice.

        Args:
            dbname: Database name.
            encoding: Ignored (see above).

        Returns:
            str: CREATE DATABASE command.
        """
        return f'CREATE DATABASE "{dbname}" CHARACTER SET utf8mb4;'

    def create_schema_sql(self, schema_name):
        """Generate CREATE SCHEMA (equivalent to CREATE DATABASE in MySQL).

        Args:
            schema_name: Schema name.

        Returns:
            str: CREATE SCHEMA command.
        """
        return f'CREATE SCHEMA "{schema_name}";'

    def alter_column_sql(self, column_name, new_sql_type):
        """Generate MODIFY COLUMN (type only).

        KNOWN LIMITATION: MODIFY without the full definition drops
        NOT NULL/DEFAULT. v1 accepts this (module docstring).

        Args:
            column_name: Column name.
            new_sql_type: New SQL type.

        Returns:
            str: SQL fragment.
        """
        return f'MODIFY COLUMN "{column_name}" {new_sql_type}'

    def alter_column_with_conversion_sql(self, column_name, new_sql_type,
                                         conversion_expression):
        """Not supported: MySQL has no USING clause (raises).

        Args:
            column_name: Column name.
            new_sql_type: New SQL type.
            conversion_expression: Conversion expression.

        Raises:
            SqlMigrationError: always.
        """
        raise SqlMigrationError(
            'MySQL has no USING clause for type conversions '
            f'(column "{column_name}")'
        )

    def add_not_null_sql(self, column_name):
        """Not expressible without the full definition (raises).

        Args:
            column_name: Column name.

        Raises:
            SqlMigrationError: always (v1 limitation).
        """
        raise SqlMigrationError(
            'MySQL cannot add NOT NULL without the full column definition '
            f'(column "{column_name}")'
        )

    def drop_not_null_sql(self, column_name):
        """Not expressible without the full definition (raises).

        Args:
            column_name: Column name.

        Raises:
            SqlMigrationError: always (v1 limitation).
        """
        raise SqlMigrationError(
            'MySQL cannot drop NOT NULL without the full column definition '
            f'(column "{column_name}")'
        )

    def constraint_sql(self, constraint_name, constraint_type, columns=None,
                       check_clause=None):
        """Generate a CONSTRAINT definition (UNIQUE or CHECK).

        CHECK constraints are enforced from MySQL 8.0.16.

        Args:
            constraint_name: Constraint name.
            constraint_type: "UNIQUE" or "CHECK".
            columns: Column list (for UNIQUE).
            check_clause: Clause (for CHECK).

        Returns:
            str: Constraint SQL definition.
        """
        if constraint_type == "UNIQUE":
            columns_str = ', '.join(f'"{col}"' for col in columns)
            return f'CONSTRAINT "{constraint_name}" UNIQUE ({columns_str})'
        elif constraint_type == "CHECK":
            return f'CONSTRAINT "{constraint_name}" CHECK ({check_clause})'
        raise ValueError(f"Unsupported constraint type: {constraint_type}")

    def drop_constraint_sql(self, constraint_name):
        """Generate DROP CONSTRAINT (MySQL 8.0.19+).

        Args:
            constraint_name: Constraint name.

        Returns:
            str: SQL fragment.
        """
        return f'DROP CONSTRAINT "{constraint_name}"'

    def foreign_key_sql(self, fk_name, columns, related_table, related_schema,
                        related_columns, on_delete=None, on_update=None,
                        deferrable=False, initially_deferred=False):
        """Generate a FOREIGN KEY definition.

        MySQL has no deferrable foreign keys; the deferrable arguments
        always arrive False (stripped by the shared code) and are ignored.

        Args:
            fk_name: FK constraint name.
            columns: Source columns.
            related_table: Target table.
            related_schema: Target schema.
            related_columns: Target columns.
            on_delete: ON DELETE action.
            on_update: ON UPDATE action.
            deferrable: Ignored (always False).
            initially_deferred: Ignored (always False).

        Returns:
            str: Full FK SQL definition.
        """
        columns_str = ', '.join(f'"{col}"' for col in columns)
        related_columns_str = ', '.join(f'"{col}"' for col in related_columns)
        on_delete_str = f" ON DELETE {on_delete}" if on_delete else ""
        on_update_str = f" ON UPDATE {on_update}" if on_update else ""
        return (
            f'CONSTRAINT "{fk_name}" FOREIGN KEY ({columns_str}) '
            f'REFERENCES "{related_schema}"."{related_table}" '
            f'({related_columns_str})'
            f'{on_delete_str}{on_update_str}'
        )

    def create_index_sql(self, schema_name, table_name, columns,
                         index_name=None, unique=False, method=None,
                         with_options=None, tablespace=None, where=None):
        """Generate CREATE INDEX.

        ``method``, ``with_options``, ``tablespace`` and ``where`` arrive
        stripped (MySQL lacks these capabilities) and are ignored.

        Args:
            schema_name: Schema name.
            table_name: Table name.
            columns: Dict {column: sort_order} or list of columns.
            index_name: Index name.
            unique: If True, UNIQUE index.
            method: Ignored.
            with_options: Ignored.
            tablespace: Ignored.
            where: Ignored.

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

        full_table_name = self.table_fullname(schema_name, table_name)
        unique_clause = ' UNIQUE ' if unique else " "
        return (
            f"CREATE{unique_clause}INDEX {index_name} "
            f"ON {full_table_name} ({column_list});"
        )

    def drop_table_pkey_sql(self, schema_name, table_name):
        """Generate DROP of the PRIMARY KEY.

        Args:
            schema_name: Schema name.
            table_name: Table name.

        Returns:
            str: SQL command.
        """
        full_table = self.table_fullname(schema_name, table_name)
        return f"ALTER TABLE {full_table} DROP PRIMARY KEY;"

    def add_table_pkey_sql(self, schema_name, table_name, pkeys):
        """Generate ADD PRIMARY KEY.

        Args:
            schema_name: Schema name.
            table_name: Table name.
            pkeys: Comma-separated column names.

        Returns:
            str: SQL command.
        """
        full_table = self.table_fullname(schema_name, table_name)
        return f'ALTER TABLE {full_table} ADD PRIMARY KEY ({pkeys});'

    def comment_on_column_sql(self, schema_name, table_name, column_name,
                              comment):
        """Not supported: comments are stripped by the shared code (raises).

        Raises:
            SqlMigrationError: always.
        """
        raise SqlMigrationError('MySQL dialect does not emit column comments')

    def comment_on_table_sql(self, schema_name, table_name, comment):
        """Not supported: comments are stripped by the shared code (raises).

        Raises:
            SqlMigrationError: always.
        """
        raise SqlMigrationError('MySQL dialect does not emit table comments')

    def create_extension_sql(self, extension_name):
        """Not supported: extensions are stripped by the shared code (raises).

        Raises:
            SqlMigrationError: always.
        """
        raise SqlMigrationError('MySQL has no extensions')

    def execute(self, sql, auto_commit=False, manager=False):
        """Execution belongs to the adapter (raises).

        Raises:
            SqlMigrationError: always.
        """
        raise SqlMigrationError('MysqlWriter does not execute SQL; use MysqlAdapter')
