"""Concrete PostgreSQL ``Database``/``BaseAdapter`` pair for the test suite.

The package ships the abstract facade (``database.py``) plus the dialect
building blocks (``PgWriter`` for DDL, introspection queries in the legacy
adapter format). This module wires them to a real PostgreSQL via psycopg 3
and is the first real consumer of the facade — the reference implementation
for the producer guide (roadmap doc ``04``).

The ``struct_get_*`` introspection methods are ported from the legacy
``_gnrbasepostgresadapter.py`` (genropy ``develop``) because ``DbExtractor``
consumes their exact row-dict format. The only change is ``IN %s`` →
``= ANY(%s)`` (psycopg 3 does not expand tuples into IN lists).
"""

from collections import defaultdict

import psycopg

from genro_sqlmigration.database import BaseAdapter, Database
from genro_sqlmigration.exceptions import (
    NonExistingDbException,
    SqlConnectionException,
)
from genro_sqlmigration.writers import PgWriter

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


class PgTestAdapter(BaseAdapter):
    """Adapter for PostgreSQL: PgWriter for DDL, psycopg 3 for execution."""

    TYPE_CONVERSIONS = PgWriter.TYPE_CONVERSIONS

    def __init__(self, database):
        self.database = database
        self.writer = PgWriter()

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

    def raw_fetch(self, sql, params=None):
        """Fetch all rows for an introspection query on a fresh connection."""
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchall()
        finally:
            connection.close()

    # -- Introspection (legacy struct_get_* format) --------------------------

    def struct_get_schema_info(self, schemas=None):
        columns = []
        for row in self.raw_fetch(SCHEMA_INFO_SQL, (list(schemas),)):
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

    def struct_get_constraints(self, schemas=None):
        constraints = defaultdict(lambda: defaultdict(dict))
        for row in self.raw_fetch(PRIMARY_KEY_SQL, (list(schemas),)):
            schema_name, table_name, constraint_name, column_name, _ = row
            table_key = (schema_name, table_name)
            if "PRIMARY KEY" not in constraints[table_key]:
                constraints[table_key]["PRIMARY KEY"] = {
                    "constraint_name": constraint_name,
                    "constraint_type": "PRIMARY KEY",
                    "columns": [],
                }
            constraints[table_key]["PRIMARY KEY"]["columns"].append(column_name)

        for row in self.raw_fetch(UNIQUE_CONSTRAINT_SQL, (list(schemas),)):
            schema_name, table_name, constraint_name, column_name, _ = row
            table_key = (schema_name, table_name)
            if constraint_name not in constraints[table_key]["UNIQUE"]:
                constraints[table_key]["UNIQUE"][constraint_name] = {
                    "constraint_name": constraint_name,
                    "constraint_type": "UNIQUE",
                    "columns": [],
                }
            constraints[table_key]["UNIQUE"][constraint_name]["columns"].append(column_name)

        for row in self.raw_fetch(FOREIGN_KEY_SQL, (list(schemas),)):
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

        for row in self.raw_fetch(CHECK_CONSTRAINT_SQL, (list(schemas),)):
            schema_name, table_name, constraint_name, check_clause = row
            table_key = (schema_name, table_name)
            constraints[table_key]["CHECK"][constraint_name] = {
                "constraint_name": constraint_name,
                "constraint_type": "CHECK",
                "check_clause": check_clause,
            }
        return constraints

    def struct_get_indexes(self, schemas=None):
        indexes = defaultdict(dict)
        for row in self.raw_fetch(INDEXES_SQL, (list(schemas),)):
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

    def struct_get_extensions(self):
        extensions = {}
        for row in self.raw_fetch(EXTENSIONS_SQL, ()):
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

    def struct_get_event_triggers(self):
        event_triggers = {}
        for row in self.raw_fetch(EVENT_TRIGGERS_SQL, ()):
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

    def struct_is_empty_column(self, schema_name, table_name, column_name):
        sql = (
            f'SELECT COUNT(*) = 0 FROM "{schema_name}"."{table_name}" '
            f'WHERE "{column_name}" IS NOT NULL'
        )
        rows = self.raw_fetch(sql)
        return rows[0][0] if rows else False

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
