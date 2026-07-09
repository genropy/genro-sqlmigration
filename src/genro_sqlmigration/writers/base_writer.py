# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""
base_writer.py - Interfaccia base per i writer SQL
=====================================================

Definisce l'interfaccia che ogni writer database-specifico deve implementare.
Il writer è responsabile di generare frammenti SQL corretti per il database
target (definizioni di colonne, constraint, indici, ALTER TABLE, ecc.).
"""

from abc import ABC, abstractmethod


class BaseWriter(ABC):
    """Interfaccia base per i writer di generazione SQL.

    Ogni writer genera SQL specifico per un database engine.
    I metodi restituiscono frammenti SQL (non eseguono nulla).
    """

    # Dizionario delle conversioni di tipo supportate.
    # Chiave: (old_dtype, new_dtype), Valore: None/True (semplice) o str (espressione USING).
    TYPE_CONVERSIONS = {}

    # Capability flags declared by each dialect writer. Representation
    # capabilities gate what the migrator keeps in the ORM structure before
    # the diff (an attribute the dialect cannot store never appears on the
    # DB side, so keeping it would produce a permanent false diff); DDL
    # operation capabilities gate command generation in the command builder.
    # Vocabulary:
    #   representation: 'extensions', 'event_triggers', 'comments',
    #     'foreign_keys', 'table_constraints', 'fk_deferrable',
    #     'index_where', 'index_method', 'index_tablespace',
    #     'index_with_options'
    #   DDL operations: 'alter_column_type', 'drop_constraint',
    #     'add_constraint'
    CAPABILITIES = frozenset()

    def add_column_sql(self, column_definition):
        """Frammento ALTER TABLE che aggiunge una colonna (default PostgreSQL).

        I dialetti senza la parola chiave COLUMN (es. MSSQL) fanno override.
        """
        return f'ADD COLUMN {column_definition}'

    def alter_table_commands(self, schema_name, table_name, column_fragments):
        """Assembla gli statement ALTER TABLE dai frammenti colonna.

        Default: un solo statement con i frammenti uniti da virgola
        (stile PostgreSQL). I dialetti con ALTER TABLE a singola azione
        (es. SQLite) fanno override emettendo uno statement per frammento.
        """
        joined = ',\n'.join(column_fragments)
        return [
            f'ALTER TABLE {self.table_fullname(schema_name, table_name)}\n'
            f'{joined};'
        ]

    @abstractmethod
    def column_sql_type(self, dtype, size=None):
        """Restituisce il tipo SQL per un dtype e size dati.

        Args:
            dtype: Tipo dtype normalizzato (es. 'T', 'I', 'N', 'C').
            size: Size della colonna (opzionale).

        Returns:
            str: Tipo SQL (es. 'varchar(100)', 'integer', 'numeric(10,2)').
        """
        ...

    @abstractmethod
    def column_sql_definition(self, column_name, dtype, size=None,
                              notnull=False, default=None,
                              extra_sql=None, generated_expression=None):
        """Genera la definizione SQL completa di una colonna.

        Args:
            column_name: Nome della colonna.
            dtype: Tipo dtype normalizzato.
            size: Size della colonna.
            notnull: Se True, aggiunge NOT NULL.
            default: Valore default SQL.
            extra_sql: SQL aggiuntivo da appendere.
            generated_expression: Espressione per colonne generate.

        Returns:
            str: Definizione SQL completa (es. ``"name" varchar(100) NOT NULL``).
        """
        ...

    @abstractmethod
    def table_fullname(self, schema_name, table_name):
        """Genera il nome SQL completo di una tabella.

        Args:
            schema_name: Nome dello schema.
            table_name: Nome della tabella.

        Returns:
            str: Nome qualificato (es. ``"myschema"."mytable"``).
        """
        ...

    @abstractmethod
    def create_db_sql(self, dbname, encoding='UNICODE'):
        """Genera il comando CREATE DATABASE.

        Args:
            dbname: Nome del database.
            encoding: Encoding del database.

        Returns:
            str: Comando SQL CREATE DATABASE.
        """
        ...

    @abstractmethod
    def create_schema_sql(self, schema_name):
        """Genera il comando CREATE SCHEMA.

        Args:
            schema_name: Nome dello schema.

        Returns:
            str: Comando SQL CREATE SCHEMA.
        """
        ...

    @abstractmethod
    def alter_column_sql(self, column_name, new_sql_type):
        """Genera il frammento ALTER COLUMN TYPE.

        Args:
            column_name: Nome della colonna.
            new_sql_type: Nuovo tipo SQL.

        Returns:
            str: Frammento SQL (es. ``ALTER COLUMN "col" TYPE varchar(200)``).
        """
        ...

    @abstractmethod
    def alter_column_with_conversion_sql(self, column_name, new_sql_type,
                                         conversion_expression):
        """Genera il frammento ALTER COLUMN TYPE con USING.

        Args:
            column_name: Nome della colonna.
            new_sql_type: Nuovo tipo SQL.
            conversion_expression: Espressione di conversione.

        Returns:
            str: Frammento SQL con clausola USING.
        """
        ...

    @abstractmethod
    def add_not_null_sql(self, column_name):
        """Genera il frammento per aggiungere NOT NULL.

        Args:
            column_name: Nome della colonna.

        Returns:
            str: Frammento SQL.
        """
        ...

    @abstractmethod
    def drop_not_null_sql(self, column_name):
        """Genera il frammento per rimuovere NOT NULL.

        Args:
            column_name: Nome della colonna.

        Returns:
            str: Frammento SQL.
        """
        ...

    @abstractmethod
    def constraint_sql(self, constraint_name, constraint_type, columns=None,
                       check_clause=None):
        """Genera la definizione SQL di un constraint.

        Args:
            constraint_name: Nome del constraint.
            constraint_type: Tipo ("UNIQUE", "CHECK").
            columns: Lista colonne (per UNIQUE).
            check_clause: Clausola CHECK (per CHECK).

        Returns:
            str: Definizione SQL del constraint.
        """
        ...

    @abstractmethod
    def drop_constraint_sql(self, constraint_name):
        """Genera il frammento DROP CONSTRAINT.

        Args:
            constraint_name: Nome del constraint.

        Returns:
            str: Frammento SQL.
        """
        ...

    @abstractmethod
    def foreign_key_sql(self, fk_name, columns, related_table, related_schema,
                        related_columns, on_delete=None, on_update=None,
                        deferrable=False, initially_deferred=False):
        """Genera la definizione SQL di una FOREIGN KEY.

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
            str: Definizione SQL della FK.
        """
        ...

    @abstractmethod
    def create_index_sql(self, schema_name, table_name, columns,
                         index_name=None, unique=False, method=None,
                         with_options=None, tablespace=None, where=None):
        """Genera il comando CREATE INDEX.

        Args:
            schema_name: Nome dello schema.
            table_name: Nome della tabella.
            columns: Dict {colonna: sort_order} o lista colonne.
            index_name: Nome dell'indice.
            unique: Se True, indice UNIQUE.
            method: Metodo (btree, gin, gist, ecc.).
            with_options: Opzioni WITH.
            tablespace: Tablespace.
            where: Condizione WHERE (partial index).

        Returns:
            str: Comando SQL CREATE INDEX.
        """
        ...

    @abstractmethod
    def drop_table_pkey_sql(self, schema_name, table_name):
        """Genera il comando DROP della primary key.

        Args:
            schema_name: Nome dello schema.
            table_name: Nome della tabella.

        Returns:
            str: Comando SQL.
        """
        ...

    @abstractmethod
    def add_table_pkey_sql(self, schema_name, table_name, pkeys):
        """Genera il comando ADD PRIMARY KEY.

        Args:
            schema_name: Nome dello schema.
            table_name: Nome della tabella.
            pkeys: Stringa di colonne separate da virgola.

        Returns:
            str: Comando SQL.
        """
        ...

    @abstractmethod
    def create_extension_sql(self, extension_name):
        """Genera il comando CREATE EXTENSION.

        Args:
            extension_name: Nome dell'estensione.

        Returns:
            str: Comando SQL.
        """
        ...

    @abstractmethod
    def comment_on_column_sql(self, schema_name, table_name, column_name,
                              comment):
        """Genera il comando COMMENT ON COLUMN.

        Args:
            schema_name: Nome dello schema.
            table_name: Nome della tabella.
            column_name: Nome della colonna.
            comment: Testo del commento, o None per rimuoverlo.

        Returns:
            str: Comando SQL.
        """
        ...

    @abstractmethod
    def comment_on_table_sql(self, schema_name, table_name, comment):
        """Genera il comando COMMENT ON TABLE.

        Args:
            schema_name: Nome dello schema.
            table_name: Nome della tabella.
            comment: Testo del commento, o None per rimuoverlo.

        Returns:
            str: Comando SQL.
        """
        ...

    @abstractmethod
    def execute(self, sql, auto_commit=False, manager=False):
        """Esegue un comando SQL sul database.

        Args:
            sql: Comando SQL da eseguire.
            auto_commit: Se True, commit automatico.
            manager: Se True, usa connessione di sistema (per CREATE DATABASE).
        """
        ...
