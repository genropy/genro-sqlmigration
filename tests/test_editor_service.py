# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""Round-trip on a live database: migrate from XML, then introspect back to XML.

Exercises editor_service against an ephemeral PostgreSQL (the pg_server
fixture): an edited SQL-model XML creates the tables, and introspecting the
live database yields XML that describes them — the DB → XML → edit → migrate
loop end to end.
"""

import pytest

from genro_sqlmigration.editor_service import introspect_to_xml, migrate_from_xml

SEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<db xmlns="urn:genro:sql-model:1.0" name="test">
  <schema name="public">
    <table name="author" pkey="id">
      <column name="id" dtype="serial" notnull="true"/>
      <column name="name" dtype="A" size="0:120" notnull="true"/>
    </table>
    <table name="recipe" pkey="id">
      <column name="id" dtype="serial" notnull="true"/>
      <column name="title" dtype="A" size="0:80" notnull="true"/>
      <column name="author_id" dtype="L">
        <relation to="public.author.id" on_delete="CASCADE"/>
      </column>
    </table>
  </schema>
</db>"""


@pytest.fixture
def conn(pg_server):
    params = dict(pg_server)
    params.setdefault("dbname", "test")  # testing.postgresql default database
    return params


def test_migrate_then_introspect_roundtrip(conn):
    # empty DB -> the edited XML creates author + recipe
    changes = migrate_from_xml(conn, SEED_XML, apply=True, schemas=["public"])
    assert "CREATE TABLE" in changes

    # introspect the live DB back to XML: it must describe both tables
    xml = introspect_to_xml(conn, schemas=["public"])
    assert 'name="author"' in xml
    assert 'name="recipe"' in xml
    assert 'to="public.author.id"' in xml  # the FK survived the round-trip
