# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""
readers - Database introspection modules
=========================================

Readers read the **actual** structure of a database and produce the
normalized JSON defined in :mod:`structures`.

Each reader is specific to a database engine:

- :class:`PgReader`: PostgreSQL (uses information_schema and pg_catalog)
- :class:`SqliteReader`: SQLite (sqlite_master and PRAGMA queries)
- :class:`MysqlReader`: MySQL (information_schema)
- :class:`MssqlReader`: Microsoft SQL Server (sys.* catalog views)

All readers inherit from :class:`BaseReader`, which declares the shared
introspection flow driven by :meth:`get_json_struct`.
"""

from genro_sqlmigration.readers.base_reader import BaseReader
from genro_sqlmigration.readers.mssql_reader import MssqlReader
from genro_sqlmigration.readers.mysql_reader import MysqlReader
from genro_sqlmigration.readers.pg_reader import PgReader
from genro_sqlmigration.readers.sqlite_reader import SqliteReader

__all__ = [
    "BaseReader",
    "MssqlReader",
    "MysqlReader",
    "PgReader",
    "SqliteReader",
]
