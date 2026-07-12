genro-sqlmigration
==================

An autonomous Python library for SQL schema migrations: compare a
normalized JSON description of a database (the contract) against a live
database and generate or apply the realignment SQL. ORM-agnostic by
design — any producer that emits the JSON contract can drive it.

Supported dialects: PostgreSQL, SQLite, MySQL, MSSQL.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   producer_guide
