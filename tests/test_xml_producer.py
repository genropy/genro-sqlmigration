# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""XML producer tests: a clean SQL-model XML projects to valid migrator JSON."""

from genro_sqlmigration import StructureValidator
from genro_sqlmigration.json_producer import JsonStructureProducer
from genro_sqlmigration.structures import json_equal
from genro_sqlmigration.xml_producer import XmlStructureProducer, struct_to_xml
from tests.test_json_producer import MULTICOL_JSON, NAMED_JSON

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


# --- hard-gap constructs: XML twin of MULTICOL_JSON (the JSON oracle) --------

# Equivalent of tests.test_json_producer.MULTICOL_JSON: multi-column FK
# (table-level <relation> with <to> children), an index with per-column sort
# and WITH options (<column>/<option> children), and a root event trigger.
MULTICOL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<db xmlns="urn:genro:sql-model:1.0" name="hg">
  <schema name="s">
    <table name="parent" pkey="a,b">
      <column name="a" dtype="I"/>
      <column name="b" dtype="I"/>
    </table>
    <table name="child" pkey="id">
      <column name="id" dtype="serial"/>
      <column name="pa" dtype="I"/>
      <column name="pb" dtype="I"/>
      <column name="created" dtype="D"/>
      <relation columns="pa,pb">
        <to schema="s" table="parent" column="a"/>
        <to schema="s" table="parent" column="b"/>
      </relation>
      <index>
        <column name="pa"/>
        <column name="created" sort="DESC"/>
        <option key="fillfactor" value="70"/>
      </index>
    </table>
  </schema>
  <event_trigger name="audit_ddl">
    <option key="event" value="ddl_command_end"/>
  </event_trigger>
</db>
"""


def test_hard_gaps_converge_with_json_twin():
    """The XML hard-gap constructs reach the same internal form as the JSON."""
    from_xml = XmlStructureProducer(MULTICOL_XML).get_json_struct()
    from_json = JsonStructureProducer(MULTICOL_JSON).get_json_struct()
    assert json_equal(from_xml, from_json)


def test_multicolumn_fk_from_xml():
    child = (XmlStructureProducer(MULTICOL_XML).get_json_struct()
             ["root"]["schemas"]["s"]["tables"]["child"])
    rel = list(child["relations"].values())[0]
    assert rel["attributes"]["columns"] == ["pa", "pb"]
    assert rel["attributes"]["related_columns"] == ["a", "b"]
    assert rel["entity_name"].startswith("fk_")


def test_index_sort_and_options_from_xml():
    child = (XmlStructureProducer(MULTICOL_XML).get_json_struct()
             ["root"]["schemas"]["s"]["tables"]["child"])
    index = list(child["indexes"].values())[0]
    columns = index["attributes"]["columns"]
    assert columns == {"pa": None, "created": "DESC"}
    assert list(columns.keys()) == ["pa", "created"]
    assert index["attributes"]["with_options"] == {"fillfactor": "70"}


def test_event_trigger_from_xml():
    triggers = XmlStructureProducer(MULTICOL_XML).get_json_struct()["root"]["event_triggers"]
    assert triggers["audit_ddl"]["attributes"]["event"] == "ddl_command_end"


def test_hard_gaps_roundtrip():
    struct1 = XmlStructureProducer(MULTICOL_XML).get_json_struct()
    struct2 = XmlStructureProducer(struct_to_xml(struct1)).get_json_struct()
    assert json_equal(struct1, struct2)


def test_hard_gaps_validate():
    StructureValidator().validate(XmlStructureProducer(MULTICOL_XML).get_json_struct())


# --- optional names: XML twin of NAMED_JSON ---------------------------------

# Equivalent of tests.test_json_producer.NAMED_JSON: an explicit name on a
# relation / UNIQUE / index becomes the readable name; the key stays the hash.
NAMED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<db xmlns="urn:genro:sql-model:1.0" name="nm">
  <schema name="s">
    <table name="author" pkey="id">
      <column name="id" dtype="serial"/>
    </table>
    <table name="recipe" pkey="id">
      <column name="id" dtype="serial"/>
      <column name="title" dtype="A" size="0:80"/>
      <column name="author_id" dtype="I">
        <relation to="s.author.id" name="fk_recipe_author"/>
      </column>
      <constraint name="uq_title" type="UNIQUE" columns="title"/>
      <index name="idx_title" columns="title"/>
    </table>
  </schema>
</db>
"""


def test_named_entities_converge_with_json_twin():
    from_xml = XmlStructureProducer(NAMED_XML).get_json_struct()
    from_json = JsonStructureProducer(NAMED_JSON).get_json_struct()
    assert json_equal(from_xml, from_json)


def test_named_entities_carry_readable_name_key_stays_hash_from_xml():
    recipe = (XmlStructureProducer(NAMED_XML).get_json_struct()
              ["root"]["schemas"]["s"]["tables"]["recipe"])
    (rel_key, rel), = recipe["relations"].items()
    assert rel["attributes"]["constraint_name"] == "fk_recipe_author"
    assert rel_key == rel["entity_name"] and rel_key.startswith("fk_")
    (cst_key, cst), = recipe["constraints"].items()
    assert cst["attributes"]["constraint_name"] == "uq_title"
    assert cst_key == cst["entity_name"] and cst_key.startswith("cst_")
    (idx_key, idx), = recipe["indexes"].items()
    assert idx["attributes"]["index_name"] == "idx_title"
    assert idx_key == idx["entity_name"] and idx_key.startswith("idx_")
