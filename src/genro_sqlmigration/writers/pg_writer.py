# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""
pg_writer.py - Generatore SQL per PostgreSQL
==============================================

Genera frammenti e comandi SQL specifici per PostgreSQL.

Questo writer sostituisce i metodi di generazione SQL (``columnSqlDefinition``,
``struct_constraint_sql``, ``struct_foreign_key_sql``, ecc.) che erano
negli adapter di Genropy.

Mappatura dtype -> tipo SQL PostgreSQL
---------------------------------------

::

    T  -> text
    C  -> char(size)
    A  -> varchar(max_size)
    I  -> integer
    L  -> bigint
    R  -> smallint
    N  -> numeric(precision,scale)
    B  -> boolean
    D  -> date
    DH -> timestamp without time zone
    H  -> time without time zone
    O  -> bytea
    serial -> bigserial
"""

from genro_sqlmigration.writers.base_writer import BaseWriter

# Mappatura dtype normalizzato -> tipo SQL PostgreSQL
DTYPE_TO_SQL = {
    'T': 'text',
    'I': 'integer',
    'L': 'bigint',
    'R': 'smallint',
    'B': 'boolean',
    'D': 'date',
    'DH': 'timestamp without time zone',
    'H': 'time without time zone',
    'O': 'bytea',
    'serial': 'bigserial',
}

# Conversioni di tipo supportate.
# Chiave: (old_dtype, new_dtype)
# Valore: None/True = conversione semplice, str = espressione USING
PG_TYPE_CONVERSIONS = {
    ('T', 'I'): 'CASE WHEN {column_name} ~ \'^[0-9]+$\' '
                'THEN {column_name}::integer ELSE NULL END',
    ('T', 'L'): 'CASE WHEN {column_name} ~ \'^[0-9]+$\' '
                'THEN {column_name}::bigint ELSE NULL END',
    ('T', 'N'): 'CASE WHEN {column_name} ~ \'^[0-9]+(\\.[0-9]+)?$\' '
                'THEN {column_name}::numeric ELSE NULL END',
    ('T', 'B'): 'CASE WHEN {column_name} IN (\'true\',\'t\',\'1\',\'yes\') THEN true '
                'WHEN {column_name} IN (\'false\',\'f\',\'0\',\'no\') THEN false '
                'ELSE NULL END',
    ('T', 'D'): 'CASE WHEN {column_name} ~ \'^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$\' '
                'THEN {column_name}::date ELSE NULL END',
    ('I', 'L'): None,    # integer -> bigint: sempre sicuro
    ('I', 'N'): None,    # integer -> numeric: sempre sicuro
    ('L', 'I'): True,    # bigint -> integer: PostgreSQL trunca se overflow
    ('L', 'N'): None,    # bigint -> numeric: sempre sicuro
    ('R', 'I'): None,    # smallint -> integer: sempre sicuro
    ('R', 'L'): None,    # smallint -> bigint: sempre sicuro
    ('N', 'I'): True,    # numeric -> integer: PostgreSQL trunca
    ('N', 'L'): True,    # numeric -> bigint: PostgreSQL trunca
    ('B', 'I'): True,    # boolean -> integer: true=1, false=0
    ('D', 'DH'): None,   # date -> timestamp: sempre sicuro
    ('DH', 'D'): True,   # timestamp -> date: perde l'ora
}


class PgWriter(BaseWriter):
    """Writer di generazione SQL per PostgreSQL.

    Genera frammenti SQL conformi alla sintassi PostgreSQL.

    Args:
        connection_params: Parametri di connessione psycopg per l'esecuzione
            diretta. Se None, il writer genera solo SQL senza eseguirlo.
    """

    TYPE_CONVERSIONS = PG_TYPE_CONVERSIONS

    def __init__(self, connection_params=None):
        self.connection_params = connection_params

    def column_sql_type(self, dtype, size=None):
        """Restituisce il tipo SQL PostgreSQL per un dtype.

        Args:
            dtype: Tipo dtype normalizzato.
            size: Size della colonna.

        Returns:
            str: Tipo SQL PostgreSQL.
        """
        if dtype in DTYPE_TO_SQL:
            return DTYPE_TO_SQL[dtype]
        elif dtype == 'C':
            return f'char({size})' if size else 'text'
        elif dtype == 'A':
            if size and ':' in size:
                max_size = size.split(':')[1]
                return f'varchar({max_size})'
            return f'varchar({size})' if size else 'text'
        elif dtype == 'N':
            return f'numeric({size})' if size else 'numeric'
        return 'text'

    def column_sql_definition(self, column_name, dtype, size=None,
                              notnull=False, default=None,
                              extra_sql=None, generated_expression=None):
        """Genera la definizione SQL completa di una colonna PostgreSQL.

        Args:
            column_name: Nome della colonna.
            dtype: Tipo dtype normalizzato.
            size: Size della colonna.
            notnull: Se True o stringa non vuota, aggiunge NOT NULL.
            default: Valore default SQL.
            extra_sql: SQL aggiuntivo.
            generated_expression: Espressione per colonne GENERATED ALWAYS.

        Returns:
            str: Definizione SQL della colonna.
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
        """Genera il nome completo quotato della tabella.

        Args:
            schema_name: Nome dello schema.
            table_name: Nome della tabella.

        Returns:
            str: ``"schema"."table"``
        """
        return f'"{schema_name}"."{table_name}"'

    def create_db_sql(self, dbname, encoding='UNICODE'):
        """Genera CREATE DATABASE.

        Args:
            dbname: Nome del database.
            encoding: Encoding.

        Returns:
            str: Comando SQL.
        """
        return f"CREATE DATABASE \"{dbname}\" ENCODING '{encoding}';"

    def create_schema_sql(self, schema_name):
        """Genera CREATE SCHEMA IF NOT EXISTS.

        Args:
            schema_name: Nome dello schema.

        Returns:
            str: Comando SQL.
        """
        return f'CREATE SCHEMA IF NOT EXISTS "{schema_name}";'

    def alter_column_sql(self, column_name, new_sql_type):
        """Genera ALTER COLUMN TYPE (senza USING).

        Args:
            column_name: Nome della colonna.
            new_sql_type: Nuovo tipo SQL.

        Returns:
            str: Frammento SQL.
        """
        return f'ALTER COLUMN "{column_name}" TYPE {new_sql_type}'

    def alter_column_with_conversion_sql(self, column_name, new_sql_type,
                                         conversion_expression):
        """Genera ALTER COLUMN TYPE con USING.

        Args:
            column_name: Nome della colonna.
            new_sql_type: Nuovo tipo SQL.
            conversion_expression: Espressione di conversione.

        Returns:
            str: Frammento SQL con clausola USING.
        """
        return (
            f'ALTER COLUMN "{column_name}" TYPE {new_sql_type} '
            f'USING {conversion_expression}'
        )

    def add_not_null_sql(self, column_name):
        """Genera ALTER COLUMN SET NOT NULL.

        Args:
            column_name: Nome della colonna.

        Returns:
            str: Frammento SQL.
        """
        return f'ALTER COLUMN "{column_name}" SET NOT NULL'

    def drop_not_null_sql(self, column_name):
        """Genera ALTER COLUMN DROP NOT NULL.

        Args:
            column_name: Nome della colonna.

        Returns:
            str: Frammento SQL.
        """
        return f'ALTER COLUMN "{column_name}" DROP NOT NULL'

    def constraint_sql(self, constraint_name, constraint_type, columns=None,
                       check_clause=None):
        """Genera la definizione di un CONSTRAINT.

        Args:
            constraint_name: Nome del constraint.
            constraint_type: "UNIQUE" o "CHECK".
            columns: Lista colonne (per UNIQUE).
            check_clause: Clausola (per CHECK).

        Returns:
            str: Definizione SQL del constraint.
        """
        if constraint_type == "UNIQUE":
            columns_str = ', '.join(f'"{col}"' for col in columns)
            return f'CONSTRAINT "{constraint_name}" UNIQUE ({columns_str})'
        elif constraint_type == "CHECK":
            return f'CONSTRAINT "{constraint_name}" CHECK ({check_clause})'
        raise ValueError(f"Unsupported constraint type: {constraint_type}")

    def drop_constraint_sql(self, constraint_name):
        """Genera DROP CONSTRAINT IF EXISTS.

        Args:
            constraint_name: Nome del constraint.

        Returns:
            str: Frammento SQL.
        """
        return f'DROP CONSTRAINT IF EXISTS "{constraint_name}"'

    def foreign_key_sql(self, fk_name, columns, related_table, related_schema,
                        related_columns, on_delete=None, on_update=None,
                        deferrable=False, initially_deferred=False):
        """Genera la definizione di una FOREIGN KEY.

        Args:
            fk_name: Nome del constraint FK.
            columns: Colonne sorgente.
            related_table: Tabella target.
            related_schema: Schema target.
            related_columns: Colonne target.
            on_delete: Azione ON DELETE.
            on_update: Azione ON UPDATE.
            deferrable: Se True, FK deferrable.
            initially_deferred: Se True, FK initially deferred.

        Returns:
            str: Definizione SQL completa della FK.
        """
        columns_str = ', '.join(f'"{col}"' for col in columns)
        related_columns_str = ', '.join(f'"{col}"' for col in related_columns)
        on_delete_str = f" ON DELETE {on_delete}" if on_delete else ""
        on_update_str = f" ON UPDATE {on_update}" if on_update else ""
        deferrable_str = " DEFERRABLE" if deferrable else ""
        initially_deferred_str = (
            " INITIALLY DEFERRED" if deferrable and initially_deferred else ""
        )
        return (
            f'CONSTRAINT "{fk_name}" FOREIGN KEY ({columns_str}) '
            f'REFERENCES "{related_schema}"."{related_table}" '
            f'({related_columns_str})'
            f'{on_delete_str}{on_update_str}'
            f'{deferrable_str}{initially_deferred_str}'
        )

    def create_index_sql(self, schema_name, table_name, columns,
                         index_name=None, unique=False, method=None,
                         with_options=None, tablespace=None, where=None):
        """Genera CREATE INDEX.

        Args:
            schema_name: Nome dello schema.
            table_name: Nome della tabella.
            columns: Dict {colonna: sort_order} o lista colonne.
            index_name: Nome dell'indice.
            unique: Se True, indice UNIQUE.
            method: Metodo (default: btree).
            with_options: Dict opzioni WITH.
            tablespace: Tablespace.
            where: Condizione WHERE.

        Returns:
            str: Comando CREATE INDEX.
        """
        with_options = with_options or {}
        method = method or "btree"

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

        with_parts = [f"{key} = {value}" for key, value in with_options.items()]
        with_clause = f"WITH ({', '.join(with_parts)})" if with_parts else ""
        tablespace_clause = f"TABLESPACE {tablespace}" if tablespace else ""
        where_clause = f"WHERE {where}" if where else ""
        full_table_name = self.table_fullname(schema_name, table_name)
        unique_clause = ' UNIQUE ' if unique else " "

        sql = (
            f"CREATE{unique_clause}INDEX {index_name} "
            f"ON {full_table_name} "
            f"USING {method} ({column_list}) "
            f"{with_clause} {tablespace_clause} {where_clause}"
        )
        return f'{" ".join(sql.split())};'

    def drop_table_pkey_sql(self, schema_name, table_name):
        """Genera DROP della PRIMARY KEY.

        Args:
            schema_name: Nome dello schema.
            table_name: Nome della tabella.

        Returns:
            str: Comando SQL.
        """
        full_table = self.table_fullname(schema_name, table_name)
        return f"ALTER TABLE {full_table} DROP CONSTRAINT IF EXISTS {table_name}_pkey;"

    def add_table_pkey_sql(self, schema_name, table_name, pkeys):
        """Genera ADD PRIMARY KEY.

        Args:
            schema_name: Nome dello schema.
            table_name: Nome della tabella.
            pkeys: Stringa colonne separate da virgola.

        Returns:
            str: Comando SQL.
        """
        full_table = self.table_fullname(schema_name, table_name)
        return f'ALTER TABLE {full_table} ADD PRIMARY KEY ({pkeys});'

    def create_extension_sql(self, extension_name):
        """Genera DROP + CREATE EXTENSION.

        Args:
            extension_name: Nome dell'estensione.

        Returns:
            str: Comandi SQL.
        """
        return (
            f"DROP EXTENSION IF EXISTS {extension_name};\n"
            f"CREATE EXTENSION {extension_name};"
        )

    def execute(self, sql, auto_commit=False, manager=False):
        """Esegue un comando SQL sul database PostgreSQL.

        Args:
            sql: Comando SQL da eseguire.
            auto_commit: Se True, commit automatico.
            manager: Se True, usa connessione di sistema.
        """
        import psycopg
        if isinstance(self.connection_params, str):
            conn = psycopg.connect(self.connection_params, autocommit=auto_commit)
        else:
            conn = psycopg.connect(**self.connection_params, autocommit=auto_commit)
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
            if not auto_commit:
                conn.commit()
        finally:
            conn.close()
