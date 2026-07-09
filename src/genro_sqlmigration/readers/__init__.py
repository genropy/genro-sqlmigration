# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""
readers - Database introspection modules
=========================================

Readers read the **actual** structure of a database and produce the
normalized JSON defined in :mod:`structures`.

Each reader is specific to a database engine:

- :class:`PgReader`: PostgreSQL (uses information_schema and pg_catalog)
- SQLite reader: (future)

All readers inherit from :class:`BaseReader`, which declares the shared
introspection flow driven by :meth:`get_json_struct`.
"""

from genro_sqlmigration.readers.base_reader import BaseReader
from genro_sqlmigration.readers.pg_reader import PgReader

__all__ = [
    "BaseReader",
    "PgReader",
]
