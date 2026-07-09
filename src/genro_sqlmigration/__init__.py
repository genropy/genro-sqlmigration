# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""
genro-sqlmigration: Database schema migration engine.

Compares a desired database structure (from any ORM or schema definition)
with the actual database structure (from PostgreSQL, SQLite, etc.)
and generates the SQL commands needed to align them.

The engine is ORM-agnostic: any framework can use it by providing
its schema in the normalized JSON format defined in :mod:`structures`.

Main components:

- :class:`SqlMigrator`: Orchestrator that coordinates the full migration flow.
- :mod:`readers`: Database-specific introspection (read actual DB structure).
- :mod:`writers`: Database-specific SQL generation (write migration commands).
- :mod:`adapters`: Concrete Database/BaseAdapter pairs per dialect.
- :mod:`structures`: Normalized JSON format (the contract between components).
- :mod:`diff_engine`: Structure comparison using dictdiffer.
- :mod:`command_builder`: SQL command generation from diff events.
- :mod:`executor`: SQL assembly and execution.
"""

from genro_sqlmigration.adapters import PgAdapter, PgDatabase
from genro_sqlmigration.migrator import SqlMigrator
from genro_sqlmigration.structures import (
    FORMAT_VERSION,
    new_column_item,
    new_constraint_item,
    new_index_item,
    new_relation_item,
    new_schema_item,
    new_structure_root,
    new_table_item,
)
from genro_sqlmigration.validation import StructureValidator

__version__ = "0.1.0"

__all__ = [
    "FORMAT_VERSION",
    "PgAdapter",
    "PgDatabase",
    "SqlMigrator",
    "StructureValidator",
    "new_column_item",
    "new_constraint_item",
    "new_index_item",
    "new_relation_item",
    "new_schema_item",
    "new_structure_root",
    "new_table_item",
]
