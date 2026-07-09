# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""
sqlite_reader.py - SQLite introspection reader
===============================================

Reads the actual structure of a SQLite database via the ``PRAGMA``
family and ``sqlite_master``, returning the normalized row format
consumed by the ``process_*`` methods on :class:`BaseReader`.

Schemas
-------

SQLite has no schemas; the package maps one schema to one file. The
reader receives the schema list and the per-schema file paths, ATTACHes
every file right after connecting and then queries the ``"schema".``
namespace natively (``"schema".sqlite_master``, ``PRAGMA "schema".*``).

Declared-type fidelity
----------------------

:class:`SqliteWriter` emits PostgreSQL-compatible declared types and
SQLite returns them verbatim from ``PRAGMA table_info``; this reader
reverses them with :data:`SQLITE_TYPES_DICT`. Parsing is
case-insensitive because SQLite upper-cases the type of an ``integer``
rowid-alias primary key (``INTEGER``).

v1 limitations
--------------

- The reader maps declared types only, so an integer primary key comes
  back as dtype ``I`` (not ``serial``): migrations targeting SQLite must
  declare integer PKs as ``I``.
- Foreign keys and CHECK constraints are never emitted (the dialect
  strips them before the diff).
"""

import os
import re
import sqlite3
from collections import defaultdict

from genro_sqlmigration.exceptions import (
    NonExistingDbException,
    SqlConnectionException,
)
from genro_sqlmigration.readers.base_reader import BaseReader

# Declared type base name -> normalized dtype code.
SQLITE_TYPES_DICT = {
    'bigint': 'L',
    'blob': 'O',
    'boolean': 'B',
    'char': 'C',
    'date': 'D',
    'integer': 'I',
    'jsonb': 'jsonb',
    'numeric': 'N',
    'real': 'R',
    'text': 'T',
    'time': 'H',
    'time without time zone': 'H',
    'timestamp without time zone': 'DH',
    'timestamp with time zone': 'DHZ',
    'varchar': 'A',
}


class SqliteReader(BaseReader):
    """SQLite introspection reader.

    Args:
        connection_params: dict with ``dbname`` (absolute path of the main
            database file).
        schemas: mapping ``{schema_name: file_path}`` of the schema files
            to ATTACH. The adapter builds it from the database's known
            schemas; ``get_json_struct(dbname, schemas)`` still drives the
            flow with the schema-name list.
    """

    def __init__(self, connection_params=None, schemas=None):
        super().__init__(connection_params)
        self._conn = None
        self.schema_files = dict(schemas or {})

    def dbname(self):
        """Return the main database file path (used for exception messages)."""
        return self.connection_params.get('dbname')

    def connect(self):
        """Open the connection, ATTACH schema files, enable foreign keys.

        A missing main database file raises :class:`NonExistingDbException`
        (``sqlite3.connect`` would silently create it); any other failure
        raises :class:`SqlConnectionException`.
        """
        dbname = self.dbname()
        if not os.path.exists(dbname):
            raise NonExistingDbException(dbname)
        try:
            self._conn = sqlite3.connect(dbname)
            self._conn.execute('PRAGMA foreign_keys = ON')
            for schema_name, path in self.schema_files.items():
                self._conn.execute(
                    f'ATTACH DATABASE \'{path}\' AS "{schema_name}"'
                )
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as error:
            raise SqlConnectionException(dbname, original_error=error) from error

    def close(self):
        """Close the database connection if open."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _fetch(self, sql, params=None):
        """Execute a query and return the rows as tuples."""
        cur = self._conn.execute(sql, params or ())
        return cur.fetchall()

    def _reverse_type(self, declared):
        """Reverse a declared type text into ``(dtype, size)``.

        ``varchar(100)`` -> ('A', '0:100'); ``numeric(10,2)`` -> ('N', '10,2');
        ``char(5)`` -> ('C', '5'); unknown -> ('T', None).
        """
        if not declared:
            return 'T', None
        text = declared.strip().lower()
        match = re.match(r'^([a-z ]+?)\s*(?:\(([^)]*)\))?$', text)
        if not match:
            return 'T', None
        base = match.group(1).strip()
        args = match.group(2)
        dtype = SQLITE_TYPES_DICT.get(base, 'T')
        if dtype == 'A':
            return 'A', f'0:{args}' if args else None
        if dtype == 'C':
            return 'C', args if args else None
        if dtype == 'N':
            return 'N', args if args else None
        return dtype, None

    def fetch_base_structure(self, schemas):
        columns = []
        for schema_name in schemas:
            tables = self._fetch(
                f'SELECT name FROM "{schema_name}".sqlite_master '
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
            if not tables:
                # Register the schema even when it has no tables so the base
                # processing can decide whether to keep or drop it.
                columns.append({
                    '_pg_schema_name': schema_name,
                    '_pg_table_name': None,
                    'name': None,
                    '_pg_is_nullable': 'YES',
                })
                continue
            for (table_name,) in tables:
                for row in self._fetch(
                    f'PRAGMA "{schema_name}".table_info("{table_name}")'
                ):
                    _cid, name, declared, notnull, dflt, _pk = row
                    dtype, size = self._reverse_type(declared)
                    col = {
                        '_pg_schema_name': schema_name,
                        '_pg_table_name': table_name,
                        'name': name,
                        'dtype': dtype,
                        '_pg_is_nullable': 'NO' if notnull else 'YES',
                        'sqldefault': dflt,
                    }
                    if size is not None:
                        col['size'] = size
                    columns.append(col)
        return columns

    def fetch_constraints(self, schemas):
        constraints = defaultdict(lambda: defaultdict(dict))
        for schema_name in schemas:
            tables = self._fetch(
                f'SELECT name FROM "{schema_name}".sqlite_master '
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            for (table_name,) in tables:
                table_key = (schema_name, table_name)
                self._fetch_primary_key(schema_name, table_name, constraints[table_key])
                self._fetch_unique(schema_name, table_name, constraints[table_key])
        return constraints

    def _fetch_primary_key(self, schema_name, table_name, table_constraints):
        pk_columns = []
        for row in self._fetch(
            f'PRAGMA "{schema_name}".table_info("{table_name}")'
        ):
            _cid, name, _declared, _notnull, _dflt, pk = row
            if pk:
                pk_columns.append((pk, name))
        if pk_columns:
            pk_columns.sort()
            table_constraints["PRIMARY KEY"] = {
                "constraint_name": f'{table_name}_pkey',
                "constraint_type": "PRIMARY KEY",
                "columns": [name for _order, name in pk_columns],
            }

    def _fetch_unique(self, schema_name, table_name, table_constraints):
        for row in self._fetch(
            f'PRAGMA "{schema_name}".index_list("{table_name}")'
        ):
            _seq, index_name, unique, origin, _partial = row
            if not self._is_unique_constraint_index(index_name, unique, origin):
                continue
            info = self._fetch(
                f'PRAGMA "{schema_name}".index_info("{index_name}")'
            )
            column_names = [name for _seqno, _cid, name in info]
            table_constraints["UNIQUE"][index_name] = {
                "constraint_name": index_name,
                "constraint_type": "UNIQUE",
                "columns": column_names,
            }

    def _is_unique_constraint_index(self, index_name, unique, origin):
        """Return True if an index represents a UNIQUE constraint.

        SQLite reports inline ``UNIQUE`` columns as origin 'u' autoindexes.
        A single-column UNIQUE emitted by the shared command builder arrives
        as ``ALTER TABLE ... ADD CONSTRAINT`` which the adapter rewrites into
        a ``cst_``-named UNIQUE index (origin 'c'); it is recognized here so
        it round-trips back into the column ``unique`` attribute.
        """
        if origin == 'u':
            return True
        return bool(unique) and index_name.startswith('cst_')

    def fetch_indexes(self, schemas):
        indexes = defaultdict(dict)
        for schema_name in schemas:
            master = {
                name: sql
                for _t, name, _tbl, sql in self._fetch(
                    f"SELECT type, name, tbl_name, sql FROM "
                    f'"{schema_name}".sqlite_master WHERE type=\'index\''
                )
            }
            tables = self._fetch(
                f'SELECT name FROM "{schema_name}".sqlite_master '
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            for (table_name,) in tables:
                table_key = (schema_name, table_name)
                for row in self._fetch(
                    f'PRAGMA "{schema_name}".index_list("{table_name}")'
                ):
                    _seq, index_name, unique, origin, _partial = row
                    columns = self._index_columns(schema_name, index_name)
                    # Mark constraint-backed indexes (PK / UNIQUE, incl. the
                    # cst_-named UNIQUE index the adapter rewrites) so shared
                    # index processing skips them.
                    if origin != 'c':
                        constraint_type = origin
                    elif self._is_unique_constraint_index(index_name, unique, origin):
                        constraint_type = 'u'
                    else:
                        constraint_type = None
                    indexes[table_key][index_name] = {
                        "unique": False,
                        "columns": columns,
                        "constraint_type": constraint_type,
                        "where": self._index_where(master.get(index_name)),
                    }
        return indexes

    def _index_columns(self, schema_name, index_name):
        """Return the ordered ``{column: sort_order}`` map of an index."""
        columns = {}
        for row in self._fetch(
            f'PRAGMA "{schema_name}".index_xinfo("{index_name}")'
        ):
            _seqno, _cid, name, desc, _coll, key = row
            if not key or name is None:
                continue
            columns[name] = "DESC" if desc else None
        return columns

    def _index_where(self, index_sql):
        """Recover a partial index WHERE clause from the stored CREATE INDEX.

        SQLite stores the CREATE INDEX text verbatim, so the substring after
        the final ``) WHERE `` round-trips exactly.
        """
        if not index_sql:
            return None
        match = re.search(r'\)\s+WHERE\s+(.+)$', index_sql, re.IGNORECASE | re.DOTALL)
        return match.group(1).strip() if match else None

    def fetch_extensions(self):
        return {}

    def fetch_event_triggers(self):
        return {}

    def is_empty_column(self, schema_name, table_name, column_name):
        """Return True if the column contains only NULL values."""
        sql = (
            f'SELECT COUNT(*) = 0 FROM "{schema_name}"."{table_name}" '
            f'WHERE "{column_name}" IS NOT NULL'
        )
        try:
            self.connect()
            rows = self._fetch(sql)
            return bool(rows[0][0]) if rows else False
        finally:
            self.close()
