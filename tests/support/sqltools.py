"""SQL normalization helper shared by the oracle tests.

Ported verbatim from the legacy suite (``gnrpy/tests/sql/test_gnrsqlmigration.py``)
so the expected-SQL oracles compare identically.
"""

import re


def normalize_sql(sql):
    """Normalize SQL whitespace/spacing so oracle comparison is stable.

    Collapses whitespace runs, strips spaces around parentheses, forces a
    single space after commas and no space before semicolons.
    """
    sql = re.sub(r'\s+', ' ', sql)
    sql = re.sub(r'\s*\(\s*', '(', sql)
    sql = re.sub(r'\s*\)\s*', ')', sql)
    sql = re.sub(r'\s*,\s*', ', ', sql)
    sql = re.sub(r'\s*;\s*', ';', sql)
    return sql.strip()
