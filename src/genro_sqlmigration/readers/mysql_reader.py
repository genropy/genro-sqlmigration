# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""
mysql_reader.py - MySQL introspection reader
=============================================

Reads the actual structure of a MySQL 8 database via
``information_schema``. Queries return the normalized row format
consumed by the ``process_*`` methods on :class:`BaseReader` (keeping the
historical ``_pg_*`` row keys, which are dialect-agnostic markers).

In MySQL a schema and a database are the same object, so the application
schemas map to MySQL databases and the queries filter
``TABLE_SCHEMA`` / ``CONSTRAINT_SCHEMA`` by the requested schema list.

Every connection runs in ``ANSI_QUOTES`` mode so double-quoted
identifiers work in the ``is_empty_column`` probe.

Error contract (#655): unknown database (errno 1049) raises
:class:`NonExistingDbException`; any other operational/connection error
raises :class:`SqlConnectionException`.
"""

from collections import defaultdict

from genro_sqlmigration.exceptions import (
    NonExistingDbException,
    SqlConnectionException,
)
from genro_sqlmigration.readers.base_reader import BaseReader

# MySQL DATA_TYPE (information_schema) -> normalized dtype. tinyint(1) is
# handled specially (boolean) in fetch_base_structure.
MYSQL_TYPES_DICT = {
    'bigint': 'L',
    'blob': 'O',
    'char': 'C',
    'date': 'D',
    'datetime': 'DH',
    'decimal': 'N',
    'double': 'R',
    'float': 'R',
    'int': 'I',
    'json': 'jsonb',
    'longblob': 'O',
    'longtext': 'T',
    'mediumtext': 'T',
    'text': 'T',
    'time': 'H',
    'timestamp': 'DHZ',
    'tinyint': 'I',
    'varchar': 'A',
}

SCHEMA_INFO_SQL = """
    SELECT
        c.TABLE_SCHEMA AS schema_name,
        c.TABLE_NAME AS table_name,
        c.COLUMN_NAME AS column_name,
        c.DATA_TYPE AS data_type,
        c.CHARACTER_MAXIMUM_LENGTH AS char_max_length,
        c.NUMERIC_PRECISION AS numeric_precision,
        c.NUMERIC_SCALE AS numeric_scale,
        c.COLUMN_TYPE AS column_type,
        c.IS_NULLABLE AS is_nullable,
        c.COLUMN_DEFAULT AS column_default,
        c.EXTRA AS extra,
        c.ORDINAL_POSITION AS ordinal_position
    FROM information_schema.TABLES t
    JOIN information_schema.COLUMNS c
        ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME
    WHERE t.TABLE_TYPE = 'BASE TABLE'
        AND t.TABLE_SCHEMA IN %s
    ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION;
"""

PRIMARY_KEY_SQL = """
    SELECT
        tc.CONSTRAINT_SCHEMA AS schema_name,
        tc.TABLE_NAME AS table_name,
        tc.CONSTRAINT_NAME AS constraint_name,
        kcu.COLUMN_NAME AS column_name,
        kcu.ORDINAL_POSITION AS ordinal_position
    FROM information_schema.TABLE_CONSTRAINTS tc
    JOIN information_schema.KEY_COLUMN_USAGE kcu
        ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
        AND tc.CONSTRAINT_SCHEMA = kcu.CONSTRAINT_SCHEMA
        AND tc.TABLE_NAME = kcu.TABLE_NAME
    WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
        AND tc.CONSTRAINT_SCHEMA IN %s
    ORDER BY kcu.ORDINAL_POSITION;
"""

UNIQUE_CONSTRAINT_SQL = """
    SELECT
        tc.CONSTRAINT_SCHEMA AS schema_name,
        tc.TABLE_NAME AS table_name,
        tc.CONSTRAINT_NAME AS constraint_name,
        kcu.COLUMN_NAME AS column_name,
        kcu.ORDINAL_POSITION AS ordinal_position
    FROM information_schema.TABLE_CONSTRAINTS tc
    JOIN information_schema.KEY_COLUMN_USAGE kcu
        ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
        AND tc.CONSTRAINT_SCHEMA = kcu.CONSTRAINT_SCHEMA
        AND tc.TABLE_NAME = kcu.TABLE_NAME
    WHERE tc.CONSTRAINT_TYPE = 'UNIQUE'
        AND tc.CONSTRAINT_SCHEMA IN %s
    ORDER BY tc.CONSTRAINT_NAME, kcu.ORDINAL_POSITION;
"""

FOREIGN_KEY_SQL = """
    SELECT
        kcu.CONSTRAINT_SCHEMA AS schema_name,
        kcu.TABLE_NAME AS table_name,
        kcu.CONSTRAINT_NAME AS constraint_name,
        kcu.COLUMN_NAME AS column_name,
        kcu.ORDINAL_POSITION AS ordinal_position,
        rc.UPDATE_RULE AS on_update,
        rc.DELETE_RULE AS on_delete,
        kcu.REFERENCED_TABLE_SCHEMA AS related_schema,
        kcu.REFERENCED_TABLE_NAME AS related_table,
        kcu.REFERENCED_COLUMN_NAME AS related_column
    FROM information_schema.KEY_COLUMN_USAGE kcu
    JOIN information_schema.REFERENTIAL_CONSTRAINTS rc
        ON kcu.CONSTRAINT_NAME = rc.CONSTRAINT_NAME
        AND kcu.CONSTRAINT_SCHEMA = rc.CONSTRAINT_SCHEMA
    WHERE kcu.REFERENCED_TABLE_NAME IS NOT NULL
        AND kcu.CONSTRAINT_SCHEMA IN %s
    ORDER BY kcu.CONSTRAINT_NAME, kcu.ORDINAL_POSITION;
"""

CHECK_CONSTRAINT_SQL = """
    SELECT
        tc.CONSTRAINT_SCHEMA AS schema_name,
        tc.TABLE_NAME AS table_name,
        cc.CONSTRAINT_NAME AS constraint_name,
        cc.CHECK_CLAUSE AS check_clause
    FROM information_schema.CHECK_CONSTRAINTS cc
    JOIN information_schema.TABLE_CONSTRAINTS tc
        ON cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
        AND cc.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA
    WHERE tc.CONSTRAINT_TYPE = 'CHECK'
        AND tc.CONSTRAINT_SCHEMA IN %s
    ORDER BY cc.CONSTRAINT_NAME;
"""

INDEXES_SQL = """
    SELECT
        s.TABLE_SCHEMA AS schema_name,
        s.TABLE_NAME AS table_name,
        s.INDEX_NAME AS index_name,
        s.COLUMN_NAME AS column_name,
        s.NON_UNIQUE AS non_unique,
        s.COLLATION AS collation,
        s.SEQ_IN_INDEX AS seq_in_index,
        tc.CONSTRAINT_TYPE AS constraint_type
    FROM information_schema.STATISTICS s
    LEFT JOIN information_schema.TABLE_CONSTRAINTS tc
        ON s.TABLE_SCHEMA = tc.CONSTRAINT_SCHEMA
        AND s.TABLE_NAME = tc.TABLE_NAME
        AND s.INDEX_NAME = tc.CONSTRAINT_NAME
    WHERE s.TABLE_SCHEMA IN %s
    ORDER BY s.TABLE_SCHEMA, s.TABLE_NAME, s.INDEX_NAME, s.SEQ_IN_INDEX;
"""


class MysqlReader(BaseReader):
    """MySQL introspection reader.

    Implements the per-dialect hooks of :class:`BaseReader` with
    ``information_schema`` queries.

    Args:
        connection_params: a pymysql kwargs dict. The ``dbname`` key is
            translated to pymysql's ``database`` kwarg.
    """

    def __init__(self, connection_params=None):
        super().__init__(connection_params)
        self._conn = None

    def dbname(self):
        """Return the database name (used for exception messages)."""
        return dict(self.connection_params).get('dbname')

    def _pymysql_params(self):
        """Translate the ``dbname`` key into pymysql's ``database`` kwarg."""
        params = dict(self.connection_params)
        dbname = params.pop('dbname', None)
        if dbname is not None:
            params['database'] = dbname
        return params

    def connect(self):
        """Open a pymysql connection with the #655 error taxonomy.

        Enables ANSI_QUOTES on the session so double-quoted identifiers
        work. Unknown database (errno 1049) raises
        :class:`NonExistingDbException`; any other operational error
        raises :class:`SqlConnectionException`.
        """
        import pymysql  # optional dependency (mysql extra)
        try:
            self._conn = pymysql.connect(**self._pymysql_params())
        except pymysql.err.OperationalError as error:
            if error.args and error.args[0] == 1049:
                raise NonExistingDbException(self.dbname()) from error
            raise SqlConnectionException(
                self.dbname(), original_error=error
            ) from error
        with self._conn.cursor() as cur:
            cur.execute("SET SESSION sql_mode = CONCAT(@@sql_mode, ',ANSI_QUOTES')")

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

    def fetch_base_structure(self, schemas):
        columns = []
        for row in self._fetch(SCHEMA_INFO_SQL, (list(schemas),)):
            (schema_name, table_name, column_name, data_type,
             char_max_length, numeric_precision, numeric_scale,
             column_type, is_nullable, column_default, extra,
             _ordinal_position) = row
            col = {
                '_pg_schema_name': schema_name,
                '_pg_table_name': table_name,
                'name': column_name,
                'dtype': data_type,
                'length': char_max_length,
                '_pg_is_nullable': is_nullable,
                'sqldefault': column_default,
                '_pg_numeric_precision': numeric_precision,
                '_pg_numeric_scale': numeric_scale,
            }
            is_auto_increment = 'auto_increment' in (extra or '').lower()
            # tinyint(1) is the canonical MySQL boolean
            if data_type == 'tinyint' and (column_type or '').startswith('tinyint(1)'):
                dtype = col['dtype'] = 'B'
            else:
                dtype = col['dtype'] = MYSQL_TYPES_DICT.get(data_type, 'T')
            if is_auto_increment:
                # auto_increment default is a pseudo-default, not a real one
                col.pop('sqldefault', None)
            if dtype == 'N':
                precision = col.pop('_pg_numeric_precision', None)
                scale = col.pop('_pg_numeric_scale', None)
                if precision is not None and scale is not None:
                    col['size'] = f"{precision},{scale}"
                elif precision is not None:
                    col['size'] = f"{precision}"
            elif dtype == 'A':
                size = col.pop('length', None)
                if size:
                    col['size'] = f"0:{size}"
                else:
                    dtype = col['dtype'] = 'T'
            elif dtype == 'C':
                size = col.pop('length', None)
                if size is not None:
                    col['size'] = str(size)
            if dtype in ('I', 'L') and is_auto_increment:
                col['dtype'] = 'serial'
            columns.append(col)
        return columns

    def fetch_constraints(self, schemas):
        constraints = defaultdict(lambda: defaultdict(dict))
        for row in self._fetch(PRIMARY_KEY_SQL, (list(schemas),)):
            schema_name, table_name, constraint_name, column_name, _ = row
            table_key = (schema_name, table_name)
            if "PRIMARY KEY" not in constraints[table_key]:
                constraints[table_key]["PRIMARY KEY"] = {
                    "constraint_name": constraint_name,
                    "constraint_type": "PRIMARY KEY",
                    "columns": [],
                }
            constraints[table_key]["PRIMARY KEY"]["columns"].append(column_name)

        for row in self._fetch(UNIQUE_CONSTRAINT_SQL, (list(schemas),)):
            schema_name, table_name, constraint_name, column_name, _ = row
            table_key = (schema_name, table_name)
            if constraint_name not in constraints[table_key]["UNIQUE"]:
                constraints[table_key]["UNIQUE"][constraint_name] = {
                    "constraint_name": constraint_name,
                    "constraint_type": "UNIQUE",
                    "columns": [],
                }
            constraints[table_key]["UNIQUE"][constraint_name]["columns"].append(column_name)

        for row in self._fetch(FOREIGN_KEY_SQL, (list(schemas),)):
            (schema_name, table_name, constraint_name, column_name, _ord,
             on_update, on_delete, related_schema, related_table,
             related_column) = row
            table_key = (schema_name, table_name)
            if constraint_name not in constraints[table_key]["FOREIGN KEY"]:
                constraints[table_key]["FOREIGN KEY"][constraint_name] = {
                    "constraint_name": constraint_name,
                    "constraint_type": "FOREIGN KEY",
                    "columns": [],
                    "on_update": on_update,
                    "on_delete": on_delete,
                    "related_schema": related_schema,
                    "related_table": related_table,
                    "deferrable": False,
                    "initially_deferred": False,
                    "related_columns": [],
                }
            fk = constraints[table_key]["FOREIGN KEY"][constraint_name]
            fk["columns"].append(column_name)
            fk["related_columns"].append(related_column)

        for row in self._fetch(CHECK_CONSTRAINT_SQL, (list(schemas),)):
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
        for row in self._fetch(INDEXES_SQL, (list(schemas),)):
            (schema_name, table_name, index_name, column_name, non_unique,
             collation, _seq, constraint_type) = row
            if index_name == 'PRIMARY':
                continue
            table_key = (schema_name, table_name)
            if index_name not in indexes[table_key]:
                indexes[table_key][index_name] = {
                    "unique": not non_unique,
                    "method": None,
                    "tablespace": None,
                    "where": None,
                    "with_options": {},
                    "columns": {},
                    "constraint_type": constraint_type,
                }
            sort_order = "DESC" if collation == 'D' else None
            indexes[table_key][index_name]["columns"][column_name] = sort_order
        return indexes

    def fetch_extensions(self):
        return {}

    def fetch_event_triggers(self):
        return {}

    def is_empty_column(self, schema_name, table_name, column_name):
        """Return True if the column contains only NULL values."""
        sql = f'''
            SELECT COUNT(*) = 0 AS is_empty
            FROM "{schema_name}"."{table_name}"
            WHERE "{column_name}" IS NOT NULL
        '''
        try:
            self.connect()
            rows = self._fetch(sql)
            return bool(rows[0][0]) if rows else False
        finally:
            self.close()
