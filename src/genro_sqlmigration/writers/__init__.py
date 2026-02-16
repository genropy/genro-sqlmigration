# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""
writers - Moduli di generazione SQL
====================================

I writer generano SQL specifico per ogni database engine.
Forniscono i metodi per creare definizioni di colonne, constraint,
indici, foreign key e comandi ALTER TABLE.

- :class:`PgWriter`: Generazione SQL per PostgreSQL.
- SQLite writer: (futuro)

Tutti i writer ereditano da :class:`BaseWriter`.
"""

from genro_sqlmigration.writers.base_writer import BaseWriter
from genro_sqlmigration.writers.pg_writer import PgWriter

__all__ = [
    "BaseWriter",
    "PgWriter",
]
