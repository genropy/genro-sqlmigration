# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""
pg_reader.py - PostgreSQL introspection reader
===============================================

Reads the actual structure of a PostgreSQL database via
``information_schema`` and ``pg_catalog``. The queries are ported from the
legacy adapter and return the normalized row format consumed by the
``process_*`` methods on :class:`BaseReader`.

Error contract (#655): a missing database raises
:class:`NonExistingDbException`; any other connection failure raises
:class:`SqlConnectionException`. Nothing is swallowed — introspection
errors surface to the caller.
"""

from collections import defaultdict

from genro_sqlmigration.exceptions import (
    NonExistingDbException,
    SqlConnectionException,
)
from genro_sqlmigration.readers.base_reader import BaseReader

DEFAULT_INDEX_METHOD = 'btree'

PG_TYPES_DICT = {
    'bigint': 'L',
    'boolean': 'B',
    'bytea': 'O',
    'character varying': 'A',
    'character': 'C',
    'date': 'D',
    'double precision': 'R',
    'integer': 'I',
    'jsonb': 'jsonb',
    'money': 'M',
    'numeric': 'N',
    'real': 'R',
    'smallint': 'I',
    'text': 'T',
    'time with time zone': 'HZ',
    'time without time zone': 'H',
    'timestamp with time zone': 'DHZ',
    'timestamp without time zone': 'DH',
    'tsvector': 'TSV',
    'vector': 'VEC',
}

SCHEMA_INFO_SQL = """
    SELECT
        s.schema_name,
        t.table_name,
        c.column_name,
        CASE WHEN c.data_type = 'USER-DEFINED' THEN c.udt_name ELSE c.data_type END AS data_type,
        c.character_maximum_length,
        c.is_nullable,
        c.column_default,
        c.numeric_precision,
        c.numeric_scale,
        CASE WHEN c.table_name IS NOT NULL THEN col_description(
            format('%%I.%%I', c.table_schema, c.table_name)::regclass,
            c.ordinal_position
        ) END AS comment,
        CASE WHEN t.table_name IS NOT NULL THEN obj_description(
            format('%%I.%%I', t.table_schema, t.table_name)::regclass,
            'pg_class'
        ) END AS table_comment
    FROM information_schema.schemata s
    LEFT JOIN information_schema.tables t
        ON s.schema_name = t.table_schema
    LEFT JOIN information_schema.columns c
        ON t.table_schema = c.table_schema AND t.table_name = c.table_name
    WHERE s.schema_name = ANY(%s)
    ORDER BY s.schema_name, t.table_name, c.ordinal_position;
"""

PRIMARY_KEY_SQL = """
    SELECT
        tc.constraint_schema AS schema_name,
        tc.table_name AS table_name,
        tc.constraint_name AS constraint_name,
        kcu.column_name AS column_name,
        kcu.ordinal_position AS ordinal_position
    FROM information_schema.table_constraints AS tc
    JOIN information_schema.key_column_usage AS kcu
        ON tc.constraint_name = kcu.constraint_name
        AND tc.constraint_schema = kcu.constraint_schema
        AND tc.table_name = kcu.table_name
    WHERE tc.constraint_type = 'PRIMARY KEY'
        AND tc.constraint_schema = ANY(%s)
    ORDER BY kcu.ordinal_position;
"""

UNIQUE_CONSTRAINT_SQL = """
    SELECT
        tc.constraint_schema AS schema_name,
        tc.table_name AS table_name,
        tc.constraint_name AS constraint_name,
        kcu.column_name AS column_name,
        kcu.ordinal_position AS ordinal_position
    FROM information_schema.table_constraints AS tc
    JOIN information_schema.key_column_usage AS kcu
        ON tc.constraint_name = kcu.constraint_name
        AND tc.constraint_schema = kcu.constraint_schema
        AND tc.table_name = kcu.table_name
    WHERE tc.constraint_type = 'UNIQUE'
        AND tc.constraint_schema = ANY(%s)
    ORDER BY tc.constraint_name, kcu.ordinal_position;
"""

FOREIGN_KEY_SQL = """
    SELECT DISTINCT
        nsp1.nspname AS schema_name,
        cls1.relname AS table_name,
        con.conname AS constraint_name,
        att1.attname AS column_name,
        fk.ord AS ord,
        CASE con.confupdtype
            WHEN 'a' THEN 'NO ACTION'
            WHEN 'r' THEN 'RESTRICT'
            WHEN 'c' THEN 'CASCADE'
            WHEN 'n' THEN 'SET NULL'
            WHEN 'd' THEN 'SET DEFAULT'
        END AS on_update,
        CASE con.confdeltype
            WHEN 'a' THEN 'NO ACTION'
            WHEN 'r' THEN 'RESTRICT'
            WHEN 'c' THEN 'CASCADE'
            WHEN 'n' THEN 'SET NULL'
            WHEN 'd' THEN 'SET DEFAULT'
        END AS on_delete,
        nsp2.nspname AS related_schema,
        cls2.relname AS related_table,
        att2.attname AS related_column,
        CASE con.condeferrable WHEN TRUE THEN 'YES' ELSE 'NO' END AS deferrable,
        CASE con.condeferred WHEN TRUE THEN 'YES' ELSE 'NO' END AS initially_deferred
    FROM pg_constraint con
    JOIN pg_class cls1 ON cls1.oid = con.conrelid
    JOIN pg_namespace nsp1 ON nsp1.oid = cls1.relnamespace
    JOIN LATERAL UNNEST(con.conkey) WITH ORDINALITY AS fk(colnum, ord) ON TRUE
    JOIN pg_attribute att1 ON att1.attnum = fk.colnum AND att1.attrelid = con.conrelid
    JOIN pg_class cls2 ON cls2.oid = con.confrelid
    JOIN pg_namespace nsp2 ON nsp2.oid = cls2.relnamespace
    JOIN LATERAL UNNEST(con.confkey) WITH ORDINALITY AS ref(colnum, ord) ON fk.ord = ref.ord
    JOIN pg_attribute att2 ON att2.attnum = ref.colnum AND att2.attrelid = con.confrelid
    WHERE con.contype = 'f'
        AND nsp1.nspname = ANY(%s)
    ORDER BY con.conname, ord;
"""

CHECK_CONSTRAINT_SQL = """
    SELECT
        nsp.nspname AS schema_name,
        cls.relname AS table_name,
        con.conname AS constraint_name,
        pg_get_expr(con.conbin, con.conrelid) AS check_clause
    FROM pg_constraint con
    JOIN pg_class cls ON cls.oid = con.conrelid
    JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
    WHERE con.contype = 'c'
        AND nsp.nspname = ANY(%s)
    ORDER BY con.conname;
"""

INDEXES_SQL = """
    SELECT
        n.nspname AS schema_name,
        t.relname AS table_name,
        i.relname AS index_name,
        a.attname AS column_name,
        ix.indisunique AS is_unique,
        ix.indoption[array_position(ix.indkey, a.attnum)-1] & 1 AS desc_order,
        am.amname AS index_method,
        spc.spcname AS tablespace,
        pg_get_expr(ix.indpred, t.oid) AS where_clause,
        i.reloptions AS with_options,
        array_position(ix.indkey, a.attnum) AS ordinal_position,
        con.contype AS constraint_type
    FROM pg_class t
    JOIN pg_index ix ON t.oid = ix.indrelid
    JOIN pg_class i ON i.oid = ix.indexrelid
    JOIN pg_am am ON i.relam = am.oid
    JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(ix.indkey)
    JOIN pg_namespace n ON t.relnamespace = n.oid
    LEFT JOIN pg_tablespace spc ON i.reltablespace = spc.oid
    LEFT JOIN pg_constraint con ON con.conindid = i.oid
    WHERE t.relkind = 'r'
        AND n.nspname = ANY(%s)
    ORDER BY n.nspname, t.relname, i.relname, ordinal_position;
"""

EXTENSIONS_SQL = """
    SELECT
        e.extname AS extension_name,
        e.extversion AS version,
        e.extrelocatable AS relocatable,
        e.extconfig AS config_tables,
        e.extcondition AS conditions,
        n.nspname AS schema_name
    FROM pg_extension e
    JOIN pg_namespace n ON e.extnamespace = n.oid
    ORDER BY e.extname;
"""

EVENT_TRIGGERS_SQL = """
    SELECT
        evtname AS trigger_name,
        evtevent AS event,
        evtowner::regrole AS owner,
        obj_description(oid, 'pg_event_trigger') AS description,
        evtfoid::regprocedure AS function_name,
        evtenabled AS enabled_state,
        evttags AS event_tags
    FROM pg_event_trigger
    ORDER BY trigger_name;
"""


class PgReader(BaseReader):
    """PostgreSQL introspection reader.

    Implements the per-dialect hooks of :class:`BaseReader` with queries
    ported from the legacy adapter.

    Args:
        connection_params: a psycopg kwargs dict
            (``{"dbname": "mydb", "user": "postgres", "host": "localhost"}``)
            or a DSN string (``"postgresql://user:pass@host/dbname"``).
    """

    def __init__(self, connection_params=None):
        super().__init__(connection_params)
        self._conn = None

    def dbname(self):
        """Return the database name (used for exception messages)."""
        if isinstance(self.connection_params, dict):
            return self.connection_params.get('dbname')
        return self.connection_params

    def connect(self):
        """Open a psycopg connection with the #655 error taxonomy.

        A missing database raises :class:`NonExistingDbException`; any
        other connection failure raises :class:`SqlConnectionException`.
        """
        import psycopg  # optional dependency (postgresql extra)
        try:
            if isinstance(self.connection_params, str):
                self._conn = psycopg.connect(self.connection_params)
            else:
                self._conn = psycopg.connect(**self.connection_params)
        except psycopg.OperationalError as error:
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

    def fetch_base_structure(self, schemas):
        columns = []
        for row in self._fetch(SCHEMA_INFO_SQL, (list(schemas),)):
            (schema_name, table_name, column_name, data_type,
             char_max_length, is_nullable, column_default,
             numeric_precision, numeric_scale,
             comment, table_comment) = row
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
                'comment': comment,
                '_pg_table_comment': table_comment,
            }
            if col['sqldefault'] and col['sqldefault'].startswith('nextval('):
                col['_pg_default'] = col.pop('sqldefault')
            dtype = col['dtype'] = PG_TYPES_DICT.get(col['dtype'], 'T')
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
            if dtype == 'L' and col.get('_pg_default'):
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
             related_column, deferrable, initially_deferred) = row
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
                    "deferrable": deferrable == "YES",
                    "initially_deferred": initially_deferred == "YES",
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
            (schema_name, table_name, index_name, column_name, is_unique,
             desc_order, index_method, tablespace, where_clause,
             with_options, _ordinal_position, constraint_type) = row
            table_key = (schema_name, table_name)
            if index_name not in indexes[table_key]:
                indexes[table_key][index_name] = {
                    "unique": is_unique,
                    "method": index_method if index_method != DEFAULT_INDEX_METHOD else None,
                    "tablespace": tablespace,
                    "where": where_clause,
                    "with_options": {},
                    "columns": {},
                    "constraint_type": constraint_type,
                }
            sort_order = "DESC" if desc_order else None
            indexes[table_key][index_name]["columns"][column_name] = sort_order
            if with_options:
                for option in with_options:
                    key, value = option.split('=')
                    indexes[table_key][index_name]["with_options"][key.strip()] = value.strip()
        return indexes

    def fetch_extensions(self):
        extensions = {}
        for row in self._fetch(EXTENSIONS_SQL, ()):
            (extension_name, version, relocatable,
             config_tables, conditions, schema_name) = row
            extensions[extension_name] = {
                "version": version,
                "relocatable": relocatable,
                "config_tables": config_tables or [],
                "conditions": conditions or [],
                "schema_name": schema_name,
            }
        return extensions

    def fetch_event_triggers(self):
        event_triggers = {}
        for row in self._fetch(EVENT_TRIGGERS_SQL, ()):
            (trigger_name, event, owner, description,
             function_name, enabled_state, event_tags) = row
            event_triggers[trigger_name] = {
                "event": event,
                "owner": owner,
                "description": description,
                "function_name": function_name,
                "enabled_state": enabled_state,
                "event_tags": event_tags or [],
            }
        return event_triggers

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
            return rows[0][0] if rows else False
        finally:
            self.close()
