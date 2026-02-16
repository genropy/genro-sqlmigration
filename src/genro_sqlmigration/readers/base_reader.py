# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""
base_reader.py - Interfaccia base per i reader di database
============================================================

Definisce l'interfaccia che ogni reader database-specifico deve implementare.
Il reader è responsabile di leggere la struttura effettiva del database
e restituirla nel formato JSON normalizzato definito in :mod:`structures`.
"""

from abc import ABC, abstractmethod


class BaseReader(ABC):
    """Interfaccia base per i reader di introspezione database.

    Ogni reader deve implementare :meth:`get_json_struct` che restituisce
    la struttura del database nel formato JSON normalizzato.

    Args:
        connection_params: Parametri di connessione specifici del database.
            Il formato dipende dall'implementazione concreta.
    """

    def __init__(self, connection_params=None):
        self.connection_params = connection_params

    @abstractmethod
    def get_json_struct(self, dbname, schemas=None):
        """Legge la struttura del database e restituisce il JSON normalizzato.

        Args:
            dbname: Nome del database.
            schemas: Lista degli schemi da ispezionare. Se None, ispeziona
                tutti gli schemi disponibili.

        Returns:
            dict: Struttura JSON normalizzata con la gerarchia completa
            root -> schemas -> tables -> columns/relations/indexes/constraints.
            Restituisce ``{}`` se il database non esiste.
        """
        ...

    @abstractmethod
    def is_empty_column(self, schema_name, table_name, column_name):
        """Verifica se una colonna del database è vuota (tutti NULL).

        Usato prima delle conversioni di tipo per determinare se è sicuro
        procedere senza rischio di perdita dati.

        Args:
            schema_name: Nome dello schema.
            table_name: Nome della tabella.
            column_name: Nome della colonna.

        Returns:
            bool: True se la colonna non contiene valori non-NULL.
        """
        ...
