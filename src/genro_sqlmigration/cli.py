# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""
cli.py - the migrator command-line interface
============================================

A thin process boundary over :class:`SqlMigrator`, designed to be driven by a
producer (e.g. Genropy's ``gnr db migrate``) as a subprocess: the caller pipes
one JSON job on stdin and reads the result on stdout. This keeps the producer
and the migration engine in separate processes — the producer never imports
this package, it only runs the ``genro-sqlmigrate`` command.

Job on stdin (one JSON object)::

    {
      "connection": {                    # psycopg connection kwargs
        "dbname": "app", "host": "localhost", "port": 5432,
        "user": "...", "password": "...",
        "application_schemas": ["public"],   # schemas the migrator inspects
        "read_only_schemas": [], "tenant_schemas": []
      },
      "structure": { "root": { ... } },  # the normalized ORM structure
      "options": {                       # optional SqlMigrator flags
        "extensions": "uuid-ossp,pg_trgm",
        "force": false, "backup": false,
        "ignore_constraint_name": true,
        "excludeReadOnly": true, "removeDisabled": true
      }
    }

Subcommands:

- ``migrate``           read the job, print the migration SQL (dry-run).
- ``migrate --apply``   also execute it.
- ``check``             exit 0 if the database already matches, 1 if changes
                        are needed (analogous to ``gnr db migrate --check``).

Only PostgreSQL is wired today (the producer path Genropy needs first).
"""

import argparse
import json
import sys

from genro_sqlmigration.adapters import PgDatabase
from genro_sqlmigration.migrator import SqlMigrator

# Connection keys that are schema lists for PgDatabase, not psycopg kwargs.
_SCHEMA_KEYS = ("application_schemas", "read_only_schemas", "tenant_schemas")


def _read_job(stream):
    """Parse the single JSON job object from an input stream."""
    job = json.load(stream)
    if "connection" not in job or "structure" not in job:
        raise ValueError("job must contain 'connection' and 'structure'")
    return job


def _build_migrator(job):
    """Build a PgDatabase + SqlMigrator from a job, with the ORM structure set."""
    connection = dict(job["connection"])
    schema_lists = {k: connection.pop(k, None) for k in _SCHEMA_KEYS}
    db = PgDatabase(
        connection,
        application_schemas=schema_lists["application_schemas"],
        read_only_schemas=schema_lists["read_only_schemas"],
        tenant_schemas=schema_lists["tenant_schemas"],
    )
    migrator = SqlMigrator(db, **(job.get("options") or {}))
    migrator.ormStructure = job["structure"]
    return migrator


def _cmd_migrate(job, apply):
    migrator = _build_migrator(job)
    migrator.prepareMigrationCommands()
    sql = migrator.getChanges()
    if apply:
        migrator.applyChanges()
    return sql


def _cmd_check(job):
    """Return the diff SQL (empty string means aligned)."""
    migrator = _build_migrator(job)
    migrator.prepareMigrationCommands()
    return migrator.getChanges()


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="genro-sqlmigrate",
        description="Migrate a database to match a normalized ORM structure "
                    "read as a JSON job on stdin.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_migrate = sub.add_parser("migrate", help="print (and optionally apply) "
                                               "the migration SQL")
    p_migrate.add_argument("--apply", action="store_true",
                           help="execute the SQL, not just print it")

    sub.add_parser("check", help="exit 1 if the database needs changes, "
                                 "0 if it already matches")

    args = parser.parse_args(argv)
    job = _read_job(sys.stdin)

    if args.command == "migrate":
        sql = _cmd_migrate(job, apply=args.apply)
        if sql:
            sys.stdout.write(sql)
            if not sql.endswith("\n"):
                sys.stdout.write("\n")
        return 0

    if args.command == "check":
        sql = _cmd_check(job)
        if sql:
            sys.stdout.write(sql)
            if not sql.endswith("\n"):
                sys.stdout.write("\n")
            return 1  # changes needed
        return 0  # aligned

    parser.error(f"unknown command: {args.command}")  # unreachable


if __name__ == "__main__":
    sys.exit(main())
