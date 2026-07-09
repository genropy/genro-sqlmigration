# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""XML producer tests: a clean SQL-model XML projects to valid migrator JSON."""

from genro_sqlmigration import StructureValidator
from genro_sqlmigration.structures import json_equal
from genro_sqlmigration.xml_producer import XmlStructureProducer, struct_to_xml

RECIPE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<db xmlns="urn:genro:sql-model:1.0" name="testdb">
  <schema name="public" sqlschema="public">
    <table name="author" pkey="id" comment="Recipe authors">
      <column name="id" dtype="serial" notnull="true"/>
      <column name="name" dtype="A" size="0:120" notnull="true"/>
    </table>
    <table name="recipe" pkey="id" comment="Recipes">
      <column name="id" dtype="serial" notnull="true"/>
      <column name="title" dtype="A" size="0:80" notnull="true"/>
      <column name="author_id" dtype="L" indexed="true">
        <relation to="public.author.id" on_delete="CASCADE"/>
      </column>
      <constraint name="uq_recipe_title_author" type="UNIQUE"
                  columns="title,author_id"/>
      <constraint name="ck_recipe_title" type="CHECK"
                  check_clause="(char_length(title) > 0)"/>
      <index name="ix_recipe_title" columns="title"/>
    </table>
  </schema>
  <extension name="unaccent"/>
</db>
"""


def _struct():
    return XmlStructureProducer(RECIPE_XML).get_json_struct()


def test_xml_projects_to_valid_migrator_json():
    """The produced structure passes the full contract validation."""
    normalized = StructureValidator().validate(_struct())  # raises if invalid
    assert normalized["root"]["entity_name"] == "testdb"


def test_pkey_column_is_auto_notnull():
    recipe = _struct()["root"]["schemas"]["public"]["tables"]["recipe"]
    assert recipe["attributes"]["pkeys"] == "id"
    assert recipe["columns"]["id"]["attributes"]["notnull"] == "_auto_"


def test_foreign_key_projects_to_related_table():
    recipe = _struct()["root"]["schemas"]["public"]["tables"]["recipe"]
    relations = list(recipe["relations"].values())
    assert len(relations) == 1
    attrs = relations[0]["attributes"]
    assert attrs["related_table"] == "author"
    assert attrs["related_columns"] == ["id"]
    assert relations[0]["entity_name"].startswith("fk_")


def test_constraints_unique_and_check():
    recipe = _struct()["root"]["schemas"]["public"]["tables"]["recipe"]
    kinds = {c["attributes"]["constraint_type"]
             for c in recipe["constraints"].values()}
    assert kinds == {"UNIQUE", "CHECK"}


def test_indexed_column_and_explicit_index():
    recipe = _struct()["root"]["schemas"]["public"]["tables"]["recipe"]
    # one from the explicit <index>, one from author_id indexed="true"
    assert len(recipe["indexes"]) == 2
    assert all(name.startswith("idx_") for name in recipe["indexes"])


def test_extension_present():
    assert "unaccent" in _struct()["root"]["extensions"]


def test_roundtrip_json_xml_json():
    """XML -> JSON -> XML -> JSON produces the same JSON (idempotent pair)."""
    struct1 = XmlStructureProducer(RECIPE_XML).get_json_struct()
    xml2 = struct_to_xml(struct1)
    struct2 = XmlStructureProducer(xml2).get_json_struct()
    assert json_equal(struct1, struct2)


def test_struct_to_xml_is_valid_against_producer():
    """The regenerated XML still validates as migrator JSON."""
    struct1 = XmlStructureProducer(RECIPE_XML).get_json_struct()
    xml2 = struct_to_xml(struct1)
    StructureValidator().validate(XmlStructureProducer(xml2).get_json_struct())
