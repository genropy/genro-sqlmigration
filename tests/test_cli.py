# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""CLI tests: the ``genro-sqlmigrate`` command driven with a JSON job on stdin.

Exercises the real subprocess boundary against a live PostgreSQL database
(the ``pg_server`` fixture): a check/migrate/apply/check cycle that mirrors
how Genropy's ``gnr db migrate`` will drive the CLI. No mocks — the migrator
connects, introspects and executes for real.
"""

import io
import json

import psycopg
import pytest

from genro_sqlmigration import JsonStructureProducer
from genro_sqlmigration.cli import main

MODEL = {
    "db": "test_cli_migrate",
    "schemas": [{"name": "public", "tables": [
        {"name": "author", "pkey": "id", "columns": [
            {"name": "id", "dtype": "serial"},
            {"name": "name", "dtype": "A", "size": "0:120", "notnull": True},
        ]},
    ]}],
}


def _run(command_args, job, monkeypatch):
    """Run cli.main with the job piped on stdin; return (exit_code, stdout)."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(job)))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    code = main(command_args)
    return code, out.getvalue()


@pytest.fixture
def job(pg_server):
    """A migration job targeting a fresh database on the test PG server."""
    dbname = "test_cli_migrate"
    admin = dict(pg_server, dbname="postgres")
    with psycopg.connect(**admin, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
        conn.execute(f'CREATE DATABASE "{dbname}"')
    structure = JsonStructureProducer(MODEL).get_json_struct()
    job = {
        "connection": dict(pg_server, dbname=dbname,
                           application_schemas=["public"]),
        "structure": structure,
    }
    yield job
    with psycopg.connect(**admin, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.mark.postgresql
def test_check_reports_changes_on_empty_db(job, monkeypatch):
    code, out = _run(["check"], job, monkeypatch)
    assert code == 1  # changes needed
    assert 'CREATE TABLE "public"."author"' in out


@pytest.mark.postgresql
def test_migrate_dry_run_prints_sql_without_applying(job, monkeypatch):
    code, out = _run(["migrate"], job, monkeypatch)
    assert code == 0
    assert 'CREATE TABLE "public"."author"' in out
    # dry-run must not have created the table: a following check still reports it
    code2, _ = _run(["check"], job, monkeypatch)
    assert code2 == 1


@pytest.mark.postgresql
def test_apply_then_idempotent(job, monkeypatch):
    apply_code, apply_out = _run(["migrate", "--apply"], job, monkeypatch)
    assert apply_code == 0
    assert 'CREATE TABLE "public"."author"' in apply_out

    # after apply the database matches: check is clean, migrate is empty
    check_code, check_out = _run(["check"], job, monkeypatch)
    assert check_code == 0
    assert check_out == ""

    migrate_code, migrate_out = _run(["migrate"], job, monkeypatch)
    assert migrate_code == 0
    assert migrate_out == ""


def test_rejects_job_without_structure(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"connection": {}})))
    with pytest.raises(ValueError):
        main(["migrate"])
