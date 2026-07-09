# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""
adapters - Concrete Database/BaseAdapter pairs per dialect
===========================================================

Each dialect contributes one module (PostgreSQL today, others with M3).
"""

from genro_sqlmigration.adapters.pg_adapter import PgAdapter, PgDatabase

__all__ = ["PgAdapter", "PgDatabase"]
