# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""
readers - Moduli di introspezione database
==========================================

I reader leggono la struttura **effettiva** di un database e producono
il JSON normalizzato definito in :mod:`structures`.

Ogni reader è specifico per un database engine:

- :class:`PgReader`: PostgreSQL (usa information_schema e pg_catalog)
- SQLite reader: (futuro)

Tutti i reader ereditano da :class:`BaseReader` e implementano
il metodo :meth:`get_json_struct`.
"""

from genro_sqlmigration.readers.base_reader import BaseReader
from genro_sqlmigration.readers.pg_reader import PgReader

__all__ = [
    "BaseReader",
    "PgReader",
]
