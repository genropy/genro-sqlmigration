# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""
mssql_reader.py - Microsoft SQL Server introspection reader
============================================================

Reads the actual structure of a SQL Server database via the ``sys.*``
catalog views and emits the normalized row format consumed by the shared
``process_*`` methods on :class:`BaseReader` (the historical ``_pg_*``
row keys are preserved: they are the internal contract, not a dialect).

Error contract (#655): a missing database raises
:class:`NonExistingDbException`; any other connection failure raises
:class:`SqlConnectionException`.

Two SQL Server normalizations are applied here:

- ``nvarchar``/``nchar`` ``max_length`` is measured in BYTES (2 per
  character), so it is halved to obtain the character length; a
  ``max_length`` of ``-1`` on ``nvarchar`` marks ``nvarchar(max)`` -> T.
- default-constraint definitions are wrapped in parentheses (``((0))``);
  the enclosing pairs are stripped so the default compares clean against
  the ORM value.
"""

from collections import defaultdict

from genro_sqlmigration.exceptions import (
    NonExistingDbException,
    SqlConnectionException,
)
from genro_sqlmigration.readers.base_reader import BaseReader

# Referential action names as SQL Server prints them -> legacy action names.
FK_ACTION_MAP = {
    'NO_ACTION': 'NO ACTION',
    'CASCADE': 'CASCADE',
    'SET_NULL': 'SET NULL',
    'SET_DEFAULT': 'SET DEFAULT',
}

SCHEMA_INFO_SQL = """
    SELECT
        s.name AS schema_name,
        t.name AS table_name,
        c.name AS column_name,
        ty.name AS data_type,
        c.max_length AS max_length,
        c.precision AS numeric_precision,
        c.scale AS numeric_scale,
        c.is_nullable AS is_nullable,
        c.is_identity AS is_identity,
        dc.definition AS default_definition,
        c.column_id AS column_id
    FROM sys.schemas s
    LEFT JOIN sys.tables t ON t.schema_id = s.schema_id
    LEFT JOIN sys.columns c ON c.object_id = t.object_id
    LEFT JOIN sys.types ty ON ty.user_type_id = c.user_type_id
    LEFT JOIN sys.default_constraints dc
        ON dc.parent_object_id = c.object_id
        AND dc.parent_column_id = c.column_id
    WHERE s.name IN ({placeholders})
    ORDER BY s.name, t.name, c.column_id;
"""

PRIMARY_KEY_SQL = """
    SELECT
        s.name AS schema_name,
        t.name AS table_name,
        kc.name AS constraint_name,
        col.name AS column_name,
        ic.key_ordinal AS ordinal_position
    FROM sys.key_constraints kc
    JOIN sys.tables t ON t.object_id = kc.parent_object_id
    JOIN sys.schemas s ON s.schema_id = t.schema_id
    JOIN sys.index_columns ic
        ON ic.object_id = kc.parent_object_id AND ic.index_id = kc.unique_index_id
    JOIN sys.columns col
        ON col.object_id = ic.object_id AND col.column_id = ic.column_id
    WHERE kc.type = 'PK' AND s.name IN ({placeholders})
    ORDER BY kc.name, ic.key_ordinal;
"""

UNIQUE_CONSTRAINT_SQL = """
    SELECT
        s.name AS schema_name,
        t.name AS table_name,
        kc.name AS constraint_name,
        col.name AS column_name,
        ic.key_ordinal AS ordinal_position
    FROM sys.key_constraints kc
    JOIN sys.tables t ON t.object_id = kc.parent_object_id
    JOIN sys.schemas s ON s.schema_id = t.schema_id
    JOIN sys.index_columns ic
        ON ic.object_id = kc.parent_object_id AND ic.index_id = kc.unique_index_id
    JOIN sys.columns col
        ON col.object_id = ic.object_id AND col.column_id = ic.column_id
    WHERE kc.type = 'UQ' AND s.name IN ({placeholders})
    ORDER BY kc.name, ic.key_ordinal;
"""

FOREIGN_KEY_SQL = """
    SELECT
        s.name AS schema_name,
        t.name AS table_name,
        fk.name AS constraint_name,
        col.name AS column_name,
        fkc.constraint_column_id AS ord,
        fk.update_referential_action_desc AS on_update,
        fk.delete_referential_action_desc AS on_delete,
        rs.name AS related_schema,
        rt.name AS related_table,
        rcol.name AS related_column
    FROM sys.foreign_keys fk
    JOIN sys.tables t ON t.object_id = fk.parent_object_id
    JOIN sys.schemas s ON s.schema_id = t.schema_id
    JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
    JOIN sys.columns col
        ON col.object_id = fkc.parent_object_id
        AND col.column_id = fkc.parent_column_id
    JOIN sys.tables rt ON rt.object_id = fk.referenced_object_id
    JOIN sys.schemas rs ON rs.schema_id = rt.schema_id
    JOIN sys.columns rcol
        ON rcol.object_id = fkc.referenced_object_id
        AND rcol.column_id = fkc.referenced_column_id
    WHERE s.name IN ({placeholders})
    ORDER BY fk.name, fkc.constraint_column_id;
"""

CHECK_CONSTRAINT_SQL = """
    SELECT
        s.name AS schema_name,
        t.name AS table_name,
        cc.name AS constraint_name,
        cc.definition AS check_clause
    FROM sys.check_constraints cc
    JOIN sys.tables t ON t.object_id = cc.parent_object_id
    JOIN sys.schemas s ON s.schema_id = t.schema_id
    WHERE s.name IN ({placeholders})
    ORDER BY cc.name;
"""

INDEXES_SQL = """
    SELECT
        s.name AS schema_name,
        t.name AS table_name,
        i.name AS index_name,
        col.name AS column_name,
        i.is_unique AS is_unique,
        ic.is_descending_key AS is_descending,
        ic.key_ordinal AS ordinal_position,
        i.has_filter AS has_filter,
        i.filter_definition AS filter_definition,
        i.is_primary_key AS is_primary_key,
        i.is_unique_constraint AS is_unique_constraint
    FROM sys.indexes i
    JOIN sys.tables t ON t.object_id = i.object_id
    JOIN sys.schemas s ON s.schema_id = t.schema_id
    JOIN sys.index_columns ic
        ON ic.object_id = i.object_id AND ic.index_id = i.index_id
    JOIN sys.columns col
        ON col.object_id = ic.object_id AND col.column_id = ic.column_id
    WHERE i.type > 0 AND i.name IS NOT NULL AND ic.is_included_column = 0
        AND s.name IN ({placeholders})
    ORDER BY s.name, t.name, i.name, ic.key_ordinal;
"""


class MssqlReader(BaseReader):
    """Microsoft SQL Server introspection reader.

    Implements the per-dialect hooks of :class:`BaseReader` over the
    ``sys.*`` catalog views.

    Args:
        connection_params: a pymssql kwargs dict with ``dbname`` (translated
            to pymssql's ``database`` kwarg), plus server/user/password/port.
    """

    def __init__(self, connection_params=None):
        super().__init__(connection_params)
        self._conn = None

    def dbname(self):
        """Return the database name (used for exception messages)."""
        if isinstance(self.connection_params, dict):
            return self.connection_params.get('dbname')
        return self.connection_params

    def _pymssql_kwargs(self):
        """Translate the connection params dict to pymssql master kwargs.

        Introspection connects to ``master`` and switches with ``USE`` so a
        missing target database is reported by SQL Server error 911 rather
        than the ambiguous 18456 "Login failed" that pymssql returns for a
        direct connection to a non-existing database.
        """
        params = dict(self.connection_params)
        params.pop('dbname', None)
        params['database'] = 'master'
        return params

    def connect(self):
        """Open a pymssql connection with the #655 error taxonomy.

        Connects to ``master`` then ``USE``s the target database: a missing
        database (error 911, message "does not exist") raises
        :class:`NonExistingDbException`; any other failure raises
        :class:`SqlConnectionException`. ``SET QUOTED_IDENTIFIER ON`` is
        issued so double-quoted identifiers parse consistently.
        """
        import pymssql  # optional dependency (mssql extra)
        try:
            self._conn = pymssql.connect(**self._pymssql_kwargs())
        except pymssql.OperationalError as error:
            raise SqlConnectionException(
                self.dbname(), original_error=error
            ) from error
        try:
            with self._conn.cursor() as cur:
                cur.execute('SET QUOTED_IDENTIFIER ON')
                cur.execute(f'USE [{self.dbname()}]')
        except pymssql.OperationalError as error:
            self.close()
            if 'does not exist' in str(error).lower():
                raise NonExistingDbException(self.dbname()) from error
            raise SqlConnectionException(
                self.dbname(), original_error=error
            ) from error

    def close(self):
        """Close the database connection if open."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _fetch(self, sql, params=None):
        """Execute a query and return the rows as tuples."""
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def _schema_query(self, template, schemas):
        """Format ``template`` with an IN-list placeholder and run it."""
        schemas = list(schemas)
        placeholders = ', '.join(['%s'] * len(schemas))
        sql = template.format(placeholders=placeholders)
        return self._fetch(sql, tuple(schemas))

    def _strip_default(self, definition):
        """Strip the enclosing parenthesis pairs from a default definition.

        SQL Server wraps defaults in parentheses (``((0))``, ``('x')``);
        the outer balanced pairs are removed so the value compares clean.
        """
        if definition is None:
            return None
        value = definition.strip()
        while value.startswith('(') and value.endswith(')'):
            depth = 0
            balanced = True
            for i, ch in enumerate(value):
                if ch == '(':
                    depth += 1
                elif ch == ')':
                    depth -= 1
                    if depth == 0 and i != len(value) - 1:
                        balanced = False
                        break
            if not balanced:
                break
            value = value[1:-1].strip()
        return value

    def _map_dtype(self, col):
        """Return the normalized dtype and set the ``size`` field on ``col``.

        Applies the SQL Server type reverse map with the byte/char and
        identity/serial adjustments.
        """
        data_type = col.pop('_mssql_data_type')
        max_length = col.pop('_mssql_max_length', None)
        precision = col.pop('_mssql_numeric_precision', None)
        scale = col.pop('_mssql_numeric_scale', None)
        is_identity = col.pop('_mssql_is_identity', 0)

        if data_type == 'nvarchar':
            if max_length == -1:
                return 'T'
            col['size'] = f'0:{max_length // 2}'
            return 'A'
        if data_type == 'nchar':
            col['size'] = str(max_length // 2)
            return 'C'
        if data_type == 'int':
            return 'serial' if is_identity else 'I'
        if data_type == 'bigint':
            return 'L'
        if data_type == 'float':
            return 'R'
        if data_type in ('numeric', 'decimal'):
            if precision is not None and scale is not None:
                col['size'] = f'{precision},{scale}'
            elif precision is not None:
                col['size'] = f'{precision}'
            return 'N'
        if data_type == 'bit':
            return 'B'
        if data_type == 'date':
            return 'D'
        if data_type == 'time':
            return 'H'
        if data_type == 'datetime2':
            return 'DH'
        if data_type == 'datetimeoffset':
            return 'DHZ'
        if data_type == 'varbinary':
            return 'O'
        return 'T'

    def fetch_base_structure(self, schemas):
        columns = []
        for row in self._schema_query(SCHEMA_INFO_SQL, schemas):
            (schema_name, table_name, column_name, data_type, max_length,
             numeric_precision, numeric_scale, is_nullable, is_identity,
             default_definition, _column_id) = row
            col = {
                '_pg_schema_name': schema_name,
                '_pg_table_name': table_name,
                'name': column_name,
                '_mssql_data_type': data_type,
                '_mssql_max_length': max_length,
                '_mssql_numeric_precision': numeric_precision,
                '_mssql_numeric_scale': numeric_scale,
                '_mssql_is_identity': is_identity,
                '_pg_is_nullable': 'NO' if is_nullable == 0 else 'YES',
                'sqldefault': self._strip_default(default_definition),
            }
            if column_name is None:
                # Empty table (or empty schema): no dtype resolution.
                col.pop('_mssql_data_type', None)
                col.pop('_mssql_max_length', None)
                col.pop('_mssql_numeric_precision', None)
                col.pop('_mssql_numeric_scale', None)
                col.pop('_mssql_is_identity', None)
                col['dtype'] = None
                columns.append(col)
                continue
            col['dtype'] = self._map_dtype(col)
            columns.append(col)
        return columns

    def fetch_constraints(self, schemas):
        constraints = defaultdict(lambda: defaultdict(dict))
        for row in self._schema_query(PRIMARY_KEY_SQL, schemas):
            schema_name, table_name, constraint_name, column_name, _ = row
            table_key = (schema_name, table_name)
            if "PRIMARY KEY" not in constraints[table_key]:
                constraints[table_key]["PRIMARY KEY"] = {
                    "constraint_name": constraint_name,
                    "constraint_type": "PRIMARY KEY",
                    "columns": [],
                }
            constraints[table_key]["PRIMARY KEY"]["columns"].append(column_name)

        for row in self._schema_query(UNIQUE_CONSTRAINT_SQL, schemas):
            schema_name, table_name, constraint_name, column_name, _ = row
            table_key = (schema_name, table_name)
            if constraint_name not in constraints[table_key]["UNIQUE"]:
                constraints[table_key]["UNIQUE"][constraint_name] = {
                    "constraint_name": constraint_name,
                    "constraint_type": "UNIQUE",
                    "columns": [],
                }
            constraints[table_key]["UNIQUE"][constraint_name]["columns"].append(column_name)

        for row in self._schema_query(FOREIGN_KEY_SQL, schemas):
            (schema_name, table_name, constraint_name, column_name, _ord,
             on_update, on_delete, related_schema, related_table,
             related_column) = row
            table_key = (schema_name, table_name)
            if constraint_name not in constraints[table_key]["FOREIGN KEY"]:
                constraints[table_key]["FOREIGN KEY"][constraint_name] = {
                    "constraint_name": constraint_name,
                    "constraint_type": "FOREIGN KEY",
                    "columns": [],
                    "on_update": FK_ACTION_MAP.get(on_update, 'NO ACTION'),
                    "on_delete": FK_ACTION_MAP.get(on_delete, 'NO ACTION'),
                    "related_schema": related_schema,
                    "related_table": related_table,
                    "deferrable": False,
                    "initially_deferred": False,
                    "related_columns": [],
                }
            fk = constraints[table_key]["FOREIGN KEY"][constraint_name]
            fk["columns"].append(column_name)
            fk["related_columns"].append(related_column)

        for row in self._schema_query(CHECK_CONSTRAINT_SQL, schemas):
            schema_name, table_name, constraint_name, check_clause = row
            table_key = (schema_name, table_name)
            constraints[table_key]["CHECK"][constraint_name] = {
                "constraint_name": constraint_name,
                "constraint_type": "CHECK",
                "check_clause": check_clause,
            }
        return constraints

    def fetch_indexes(self, schemas):
        indexes = defaultdict(dict)
        for row in self._schema_query(INDEXES_SQL, schemas):
            (schema_name, table_name, index_name, column_name, is_unique,
             is_descending, _ordinal_position, has_filter, filter_definition,
             is_primary_key, is_unique_constraint) = row
            if is_primary_key:
                # PK-backing indexes are represented by the constraint.
                continue
            table_key = (schema_name, table_name)
            if index_name not in indexes[table_key]:
                # A unique-constraint-backing index carries constraint_type so
                # shared processing skips it (already a UNIQUE constraint).
                constraint_type = 'u' if is_unique_constraint else None
                indexes[table_key][index_name] = {
                    "unique": bool(is_unique),
                    "method": None,
                    "tablespace": None,
                    "where": filter_definition if has_filter else None,
                    "with_options": {},
                    "columns": {},
                    "constraint_type": constraint_type,
                }
            sort_order = "DESC" if is_descending else None
            indexes[table_key][index_name]["columns"][column_name] = sort_order
        return indexes

    def fetch_extensions(self):
        """SQL Server has no extension concept."""
        return {}

    def fetch_event_triggers(self):
        """SQL Server DDL triggers are out of scope for this dialect."""
        return {}

    def is_empty_column(self, schema_name, table_name, column_name):
        """Return True if the column contains only NULL values."""
        sql = (
            f'SELECT CASE WHEN COUNT(*) = 0 THEN 1 ELSE 0 END '
            f'FROM "{schema_name}"."{table_name}" '
            f'WHERE "{column_name}" IS NOT NULL'
        )
        try:
            self.connect()
            rows = self._fetch(sql)
            return bool(rows[0][0]) if rows else False
        finally:
            self.close()
