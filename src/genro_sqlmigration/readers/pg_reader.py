# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""
pg_reader.py - Reader di introspezione per PostgreSQL
======================================================

Legge la struttura effettiva di un database PostgreSQL usando
``information_schema`` e ``pg_catalog``, e produce il JSON normalizzato.

Questo reader sostituisce i metodi ``struct_get_*`` che erano negli
adapter di Genropy, con un'implementazione dedicata e indipendente.

Query eseguite
--------------

1. **Schema info**: colonne con tipi, nullable, default
   (da ``information_schema.columns``)
2. **Constraints**: PK, UNIQUE, FK, CHECK
   (da ``information_schema.table_constraints`` + ``key_column_usage``
   + ``referential_constraints``)
3. **Indexes**: tutti gli indici con metodo, opzioni, ordinamento
   (da ``pg_class``, ``pg_index``, ``pg_am``, ``pg_attribute``)
4. **Extensions**: estensioni installate
   (da ``pg_extension``)
5. **Event triggers**: trigger DDL
   (da ``pg_event_trigger``)

Mappatura tipi PostgreSQL -> dtype
-----------------------------------

::

    bigint, int8         -> L
    integer, int4        -> I
    smallint, int2       -> R
    numeric, decimal     -> N
    boolean              -> B
    date                 -> D
    timestamp*           -> DH
    time*                -> H
    bytea                -> O
    character varying    -> A (con size) o T (senza)
    character, char      -> C
    text                 -> T
    ARRAY                -> T
    json, jsonb          -> T
    uuid                 -> T
    (altro)              -> T
"""

from collections import defaultdict

from genro_sqlmigration.readers.base_reader import BaseReader
from genro_sqlmigration.structures import (
    new_structure_root, new_schema_item, new_table_item,
    new_column_item, new_constraint_item, new_relation_item,
    new_index_item, new_extension_item, new_event_trigger_item,
    nested_defaultdict, clean_attributes
)

# Mappatura tipi PostgreSQL -> dtype normalizzati
PG_TYPES_MAP = {
    'bigint': 'L', 'int8': 'L',
    'integer': 'I', 'int4': 'I',
    'smallint': 'R', 'int2': 'R',
    'numeric': 'N', 'decimal': 'N',
    'real': 'N', 'double precision': 'N',
    'boolean': 'B',
    'date': 'D',
    'timestamp without time zone': 'DH',
    'timestamp with time zone': 'DH',
    'time without time zone': 'H',
    'time with time zone': 'H',
    'bytea': 'O',
    'character varying': 'A',
    'character': 'C', 'char': 'C',
    'text': 'T',
    'ARRAY': 'T',
    'json': 'T', 'jsonb': 'T',
    'uuid': 'T',
    'xml': 'T',
    'inet': 'T', 'cidr': 'T', 'macaddr': 'T',
    'money': 'N',
    'interval': 'T',
    'point': 'T', 'line': 'T', 'lseg': 'T',
    'box': 'T', 'path': 'T', 'polygon': 'T', 'circle': 'T',
    'tsvector': 'T', 'tsquery': 'T',
    'bit': 'T', 'bit varying': 'T',
}

DEFAULT_INDEX_METHOD = 'btree'


class PgReader(BaseReader):
    """Reader di introspezione per database PostgreSQL.

    Legge la struttura effettiva del database usando query su
    ``information_schema`` e ``pg_catalog``.

    Args:
        connection_params: Dizionario con i parametri di connessione psycopg.
            Es: ``{"dbname": "mydb", "user": "postgres", "host": "localhost"}``.
            Oppure una stringa DSN: ``"postgresql://user:pass@host/dbname"``.
    """

    def __init__(self, connection_params=None):
        super().__init__(connection_params)
        self._conn = None

    def _connect(self):
        """Apre una connessione al database PostgreSQL."""
        import psycopg
        if isinstance(self.connection_params, str):
            self._conn = psycopg.connect(self.connection_params)
        else:
            self._conn = psycopg.connect(**self.connection_params)

    def _close(self):
        """Chiude la connessione al database."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _fetch(self, sql, params=None):
        """Esegue una query e restituisce le righe come tuple.

        Args:
            sql: Query SQL da eseguire.
            params: Parametri della query (opzionali).

        Returns:
            list: Lista di tuple con i risultati.
        """
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def get_json_struct(self, dbname, schemas=None):
        """Legge la struttura del database PostgreSQL.

        Apre una connessione, esegue le query di introspezione e chiude
        la connessione nel blocco ``finally``.

        Args:
            dbname: Nome del database.
            schemas: Lista degli schemi da ispezionare.

        Returns:
            dict: Struttura JSON normalizzata, o ``{}`` se il DB non esiste.
        """
        if not schemas:
            return {}

        json_structure = new_structure_root(dbname)
        json_schemas = json_structure['root']['schemas']

        try:
            self._connect()

            # 1. Schema info: colonne con tipi
            self._process_base_structure(json_schemas, schemas)

            # 2. Constraints: PK, UNIQUE, FK
            self._process_constraints(json_schemas, schemas)

            # 3. Indexes
            self._process_indexes(json_schemas, schemas)

            # 4. Extensions
            self._process_extensions(json_structure, schemas)

            # 5. Event triggers
            self._process_event_triggers(json_structure)

        except Exception:
            return {}
        finally:
            self._close()

        return json_structure

    def is_empty_column(self, schema_name, table_name, column_name):
        """Verifica se una colonna contiene solo NULL.

        Args:
            schema_name: Nome dello schema.
            table_name: Nome della tabella.
            column_name: Nome della colonna.

        Returns:
            bool: True se la colonna non contiene valori non-NULL.
        """
        sql = f'''
            SELECT COUNT(*) = 0 AS is_empty
            FROM "{schema_name}"."{table_name}"
            WHERE "{column_name}" IS NOT NULL
        '''
        try:
            self._connect()
            rows = self._fetch(sql)
            return rows[0][0] if rows else False
        finally:
            self._close()

    # -------------------------------------------------------------------
    # Processing interno
    # -------------------------------------------------------------------

    def _process_base_structure(self, json_schemas, schemas):
        """Legge colonne e tipi da information_schema.

        Args:
            json_schemas: Dizionario schemi da popolare.
            schemas: Lista schemi da ispezionare.
        """
        sql = """
            SELECT
                c.table_schema,
                c.table_name,
                c.column_name,
                c.data_type,
                c.character_maximum_length,
                c.is_nullable,
                c.column_default,
                c.numeric_precision,
                c.numeric_scale
            FROM information_schema.columns c
            JOIN information_schema.tables t
                ON c.table_schema = t.table_schema
                AND c.table_name = t.table_name
            WHERE t.table_type = 'BASE TABLE'
                AND c.table_schema = ANY(%s)
            ORDER BY c.table_schema, c.table_name, c.ordinal_position
        """
        # Pre-inizializza gli schemi
        for schema_name in schemas:
            json_schemas[schema_name] = None

        for row in self._fetch(sql, (schemas,)):
            (schema_name, table_name, column_name, data_type,
             char_max_length, is_nullable, column_default,
             numeric_precision, numeric_scale) = row

            if not json_schemas[schema_name]:
                json_schemas[schema_name] = new_schema_item(schema_name)
            if table_name not in json_schemas[schema_name]['tables']:
                json_schemas[schema_name]['tables'][table_name] = (
                    new_table_item(schema_name, table_name)
                )

            # Converti tipo PostgreSQL -> dtype normalizzato
            dtype = PG_TYPES_MAP.get(data_type, 'T')
            colattr = {'dtype': dtype}

            # Gestione size per tipi specifici
            if dtype == 'N' and numeric_precision is not None:
                if numeric_scale is not None:
                    colattr['size'] = f"{numeric_precision},{numeric_scale}"
                else:
                    colattr['size'] = f"{numeric_precision}"
            elif dtype == 'A' and char_max_length:
                colattr['size'] = f"0:{char_max_length}"
            elif dtype == 'C' and char_max_length:
                colattr['size'] = str(char_max_length)
            elif dtype == 'A' and not char_max_length:
                dtype = colattr['dtype'] = 'T'

            # NOT NULL
            if is_nullable == 'NO':
                colattr['notnull'] = True

            # Default (escludi nextval per serial)
            if column_default and not column_default.startswith('nextval('):
                colattr['sqldefault'] = column_default

            # Serial detection
            if dtype == 'L' and column_default and column_default.startswith('nextval('):
                colattr['dtype'] = 'serial'

            col_item = new_column_item(
                schema_name, table_name, column_name, attributes=colattr
            )
            json_schemas[schema_name]['tables'][table_name]['columns'][column_name] = col_item

        # Rimuovi schemi vuoti
        for schema_name in schemas:
            if not json_schemas[schema_name]:
                json_schemas.pop(schema_name)

    def _process_constraints(self, json_schemas, schemas):
        """Legge PK, UNIQUE e FK da information_schema.

        Args:
            json_schemas: Dizionario schemi da popolare.
            schemas: Lista schemi da ispezionare.
        """
        # PRIMARY KEY
        pk_sql = """
            SELECT tc.table_schema, tc.table_name,
                   tc.constraint_name, kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
                AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'PRIMARY KEY'
                AND tc.table_schema = ANY(%s)
            ORDER BY tc.table_schema, tc.table_name,
                     kcu.ordinal_position
        """
        pk_data = defaultdict(list)
        for row in self._fetch(pk_sql, (schemas,)):
            schema_name, table_name, _, column_name = row
            pk_data[(schema_name, table_name)].append(column_name)

        for (schema_name, table_name), columns in pk_data.items():
            if schema_name not in json_schemas:
                continue
            table_json = json_schemas[schema_name]['tables'].get(table_name)
            if not table_json:
                continue
            table_json['attributes']['pkeys'] = ','.join(columns)
            for col in columns:
                if col in table_json['columns']:
                    table_json['columns'][col]['attributes']['notnull'] = '_auto_'

        # UNIQUE constraints
        uq_sql = """
            SELECT tc.table_schema, tc.table_name,
                   tc.constraint_name, kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
                AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'UNIQUE'
                AND tc.table_schema = ANY(%s)
            ORDER BY tc.table_schema, tc.table_name,
                     tc.constraint_name, kcu.ordinal_position
        """
        uq_data = defaultdict(lambda: defaultdict(list))
        for row in self._fetch(uq_sql, (schemas,)):
            schema_name, table_name, constraint_name, column_name = row
            uq_data[(schema_name, table_name)][constraint_name].append(column_name)

        for (schema_name, table_name), constraints in uq_data.items():
            if schema_name not in json_schemas:
                continue
            table_json = json_schemas[schema_name]['tables'].get(table_name)
            if not table_json:
                continue
            for constraint_name, columns in constraints.items():
                if len(columns) == 1:
                    col = columns[0]
                    if col == table_json['attributes']['pkeys']:
                        continue
                    table_json['columns'][col]['attributes']['unique'] = True
                else:
                    const_item = new_constraint_item(
                        schema_name, table_name, columns,
                        constraint_type='UNIQUE',
                        constraint_name=constraint_name
                    )
                    table_json['constraints'][const_item['entity_name']] = const_item

        # FOREIGN KEY constraints
        fk_sql = """
            SELECT
                tc.table_schema, tc.table_name,
                tc.constraint_name,
                kcu.column_name,
                ccu.table_schema AS related_schema,
                ccu.table_name AS related_table,
                ccu.column_name AS related_column,
                rc.update_rule, rc.delete_rule,
                tc.is_deferrable, tc.initially_deferred
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
                AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
                ON tc.constraint_name = ccu.constraint_name
                AND tc.table_schema = ccu.table_schema
            JOIN information_schema.referential_constraints rc
                ON tc.constraint_name = rc.constraint_name
                AND tc.table_schema = rc.constraint_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
                AND tc.table_schema = ANY(%s)
            ORDER BY tc.table_schema, tc.table_name,
                     tc.constraint_name, kcu.ordinal_position
        """
        fk_data = defaultdict(lambda: defaultdict(lambda: {
            'columns': [], 'related_columns': [],
            'related_schema': None, 'related_table': None,
            'on_update': None, 'on_delete': None,
            'deferrable': False, 'initially_deferred': False,
            'constraint_name': None
        }))
        for row in self._fetch(fk_sql, (schemas,)):
            (schema_name, table_name, constraint_name, column_name,
             related_schema, related_table, related_column,
             update_rule, delete_rule, is_deferrable, initially_deferred) = row

            key = (schema_name, table_name)
            fk = fk_data[key][constraint_name]
            if column_name not in fk['columns']:
                fk['columns'].append(column_name)
            if related_column not in fk['related_columns']:
                fk['related_columns'].append(related_column)
            fk['related_schema'] = related_schema
            fk['related_table'] = related_table
            fk['on_update'] = update_rule
            fk['on_delete'] = delete_rule
            fk['deferrable'] = is_deferrable == 'YES'
            fk['initially_deferred'] = initially_deferred == 'YES'
            fk['constraint_name'] = constraint_name

        for (schema_name, table_name), fk_constraints in fk_data.items():
            if schema_name not in json_schemas:
                continue
            table_json = json_schemas[schema_name]['tables'].get(table_name)
            if not table_json:
                continue
            for constraint_name, fk_attrs in fk_constraints.items():
                cn = fk_attrs.pop('constraint_name')
                relation_item = new_relation_item(
                    schema_name, table_name,
                    columns=fk_attrs['columns'],
                    attributes=fk_attrs,
                    constraint_name=cn
                )
                table_json['relations'][relation_item['entity_name']] = relation_item

    def _process_indexes(self, json_schemas, schemas):
        """Legge gli indici da pg_catalog.

        Filtra gli indici creati automaticamente da constraint (PK, UNIQUE).

        Args:
            json_schemas: Dizionario schemi da popolare.
            schemas: Lista schemi da ispezionare.
        """
        sql = """
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
            ORDER BY n.nspname, t.relname, i.relname, ordinal_position
        """
        indexes = defaultdict(lambda: defaultdict(dict))
        for row in self._fetch(sql, (schemas,)):
            (schema_name, table_name, index_name, column_name, is_unique,
             desc_order, index_method, tablespace, where_clause,
             with_options, _, constraint_type) = row

            table_key = (schema_name, table_name)
            if index_name not in indexes[table_key]:
                indexes[table_key][index_name] = {
                    'unique': is_unique,
                    'method': index_method if index_method != DEFAULT_INDEX_METHOD else None,
                    'tablespace': tablespace,
                    'where': where_clause,
                    'with_options': {},
                    'columns': {},
                    'constraint_type': constraint_type
                }
            sort_order = "DESC" if desc_order else None
            indexes[table_key][index_name]['columns'][column_name] = sort_order

            if with_options:
                for option in with_options:
                    k, v = option.split('=')
                    indexes[table_key][index_name]['with_options'][k.strip()] = v.strip()

        for (schema_name, table_name), idx_dict in indexes.items():
            if schema_name not in json_schemas:
                continue
            table_json = json_schemas[schema_name]['tables'].get(table_name)
            if not table_json:
                continue
            for index_name, index_attributes in idx_dict.items():
                if index_attributes.get('constraint_type'):
                    continue
                indexed_columns = list(index_attributes['columns'].keys())
                index_item = new_index_item(
                    schema_name, table_name,
                    columns=indexed_columns,
                    attributes=index_attributes,
                    index_name=index_name
                )
                table_json['indexes'][index_item['entity_name']] = index_item

    def _process_extensions(self, json_structure, schemas):
        """Legge le estensioni PostgreSQL installate.

        Filtra le estensioni dello schema ``pg_catalog``.

        Args:
            json_structure: Struttura JSON root da popolare.
            schemas: Lista schemi (non usata direttamente).
        """
        sql = """
            SELECT e.extname, e.extversion, e.extrelocatable,
                   n.nspname AS schema_name
            FROM pg_extension e
            JOIN pg_namespace n ON e.extnamespace = n.oid
            ORDER BY e.extname
        """
        for row in self._fetch(sql):
            extension_name, version, relocatable, schema_name = row
            if schema_name == 'pg_catalog':
                continue
            extension_item = new_extension_item(extension_name)
            json_structure['root']['extensions'][extension_name] = extension_item

    def _process_event_triggers(self, json_structure):
        """Legge gli event trigger DDL.

        Args:
            json_structure: Struttura JSON root da popolare.
        """
        sql = """
            SELECT evtname, evtevent, evtowner::regrole,
                   evtfoid::regprocedure, evtenabled, evttags
            FROM pg_event_trigger
            ORDER BY evtname
        """
        for row in self._fetch(sql):
            (trigger_name, event, owner,
             function_name, enabled_state, event_tags) = row
            trigger_item = new_event_trigger_item(trigger_name)
            trigger_item['attributes'].update({
                'event': event,
                'owner': str(owner),
                'function_name': str(function_name),
                'enabled_state': enabled_state,
                'event_tags': event_tags or []
            })
            json_structure['root']['event_triggers'][trigger_name] = trigger_item
