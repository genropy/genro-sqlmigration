# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""JSON producer tests: a clean human JSON projects to valid migrator JSON.

The key oracle is the cross-check against the XML twin: for the subset the
XML 1.0 form can express, both producers must reach the identical internal
form. The remaining tests exercise the hard gaps the XML form lacks.
"""

from genro_sqlmigration import StructureValidator
from genro_sqlmigration.json_producer import JsonStructureProducer
from genro_sqlmigration.structures import hashed_name, json_equal
from genro_sqlmigration.xml_producer import XmlStructureProducer

# --- a model the XML form CAN express, in both external dialects -----------

EQUIVALENT_JSON = {
    "db": "testdb",
    "schemas": [
        {
            "name": "public",
            "tables": [
                {
                    "name": "author", "pkey": "id", "comment": "Recipe authors",
                    "columns": [
                        {"name": "id", "dtype": "serial", "notnull": True},
                        {"name": "name", "dtype": "A", "size": "0:120", "notnull": True},
                    ],
                },
                {
                    "name": "recipe", "pkey": "id", "comment": "Recipes",
                    "columns": [
                        {"name": "id", "dtype": "serial", "notnull": True},
                        {"name": "title", "dtype": "A", "size": "0:80", "notnull": True},
                        {"name": "author_id", "dtype": "L"},
                    ],
                    "relations": [
                        {"columns": ["author_id"], "related_schema": "public",
                         "related_table": "author", "related_columns": ["id"],
                         "on_delete": "CASCADE"},
                    ],
                    "constraints": [
                        {"type": "UNIQUE", "columns": ["title", "author_id"]},
                        {"type": "CHECK", "name": "ck_recipe_title",
                         "check_clause": "(char_length(title) > 0)"},
                    ],
                    "indexes": [
                        {"columns": ["title"]},
                        {"columns": ["author_id"]},
                    ],
                },
            ],
        },
    ],
    "extensions": ["unaccent"],
}

EQUIVALENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
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
      <constraint type="UNIQUE" columns="title,author_id"/>
      <constraint name="ck_recipe_title" type="CHECK"
                  check_clause="(char_length(title) > 0)"/>
      <index columns="title"/>
    </table>
  </schema>
  <extension name="unaccent"/>
</db>
"""

MINIMAL_JSON = {
    "db": "mini",
    "schemas": [
        {
            "name": "app",
            "tables": [
                {
                    "name": "item", "pkey": "id",
                    "columns": [
                        {"name": "id", "dtype": "serial"},
                        {"name": "label", "dtype": "A", "size": "0:50", "notnull": True},
                        {"name": "qty", "dtype": "I"},
                        {"name": "flag", "dtype": "B"},
                    ],
                },
            ],
        },
    ],
}


def _minimal():
    return JsonStructureProducer(MINIMAL_JSON).get_json_struct()


# --- 1. minimal DB ----------------------------------------------------------

def test_minimal_db_shape():
    item = _minimal()["root"]["schemas"]["app"]["tables"]["item"]
    assert item["attributes"]["pkeys"] == "id"
    cols = item["columns"]
    assert set(cols) == {"id", "label", "qty", "flag"}
    assert cols["id"]["attributes"]["dtype"] == "serial"
    assert cols["label"]["attributes"]["size"] == "0:50"
    assert cols["qty"]["attributes"]["dtype"] == "I"


def test_pkey_column_is_auto_notnull():
    item = _minimal()["root"]["schemas"]["app"]["tables"]["item"]
    assert item["columns"]["id"]["attributes"]["notnull"] == "_auto_"


def test_dtype_default_from_size():
    struct = JsonStructureProducer({
        "db": "d", "schemas": [{"name": "s", "tables": [{
            "name": "t", "columns": [
                {"name": "a", "size": "0:10"},  # no dtype, size present -> A
                {"name": "b"},                  # no dtype, no size -> T
            ]}]}]}).get_json_struct()
    cols = struct["root"]["schemas"]["s"]["tables"]["t"]["columns"]
    assert cols["a"]["attributes"]["dtype"] == "A"
    assert cols["b"]["attributes"]["dtype"] == "T"


# --- 2. cross-check with the XML twin (the key oracle) ----------------------

def test_json_and_xml_converge():
    """For the XML-expressible subset both producers reach one internal form."""
    from_json = JsonStructureProducer(EQUIVALENT_JSON).get_json_struct()
    from_xml = XmlStructureProducer(EQUIVALENT_XML).get_json_struct()
    assert json_equal(from_json, from_xml)


# --- 3. hard gaps (JSON-only, no XML equivalent) ----------------------------

MULTICOL_JSON = {
    "db": "hg",
    "schemas": [
        {
            "name": "s",
            "tables": [
                {"name": "parent", "pkey": "a,b", "columns": [
                    {"name": "a", "dtype": "I"}, {"name": "b", "dtype": "I"}]},
                {
                    "name": "child", "pkey": "id", "columns": [
                        {"name": "id", "dtype": "serial"},
                        {"name": "pa", "dtype": "I"},
                        {"name": "pb", "dtype": "I"},
                        {"name": "created", "dtype": "D"},
                    ],
                    "relations": [
                        {"columns": ["pa", "pb"], "related_schema": "s",
                         "related_table": "parent", "related_columns": ["a", "b"]},
                    ],
                    "indexes": [
                        {"columns": {"pa": None, "created": "DESC"},
                         "with_options": {"fillfactor": "70"}},
                    ],
                },
            ],
        },
    ],
    "event_triggers": [
        {"name": "audit_ddl", "attributes": {"event": "ddl_command_end"}},
    ],
}


def _hard_gaps():
    return JsonStructureProducer(MULTICOL_JSON).get_json_struct()


def test_multicolumn_fk():
    child = _hard_gaps()["root"]["schemas"]["s"]["tables"]["child"]
    relations = list(child["relations"].values())
    assert len(relations) == 1
    rel = relations[0]
    assert rel["attributes"]["columns"] == ["pa", "pb"]
    assert rel["attributes"]["related_columns"] == ["a", "b"]
    expected = hashed_name(schema="s", table="child",
                           columns=["pa", "pb"], obj_type="fk")
    assert rel["entity_name"] == expected


def test_index_sort_order_and_with_options():
    child = _hard_gaps()["root"]["schemas"]["s"]["tables"]["child"]
    index = list(child["indexes"].values())[0]
    columns = index["attributes"]["columns"]
    assert columns == {"pa": None, "created": "DESC"}
    assert list(columns.keys()) == ["pa", "created"]
    assert index["attributes"]["with_options"] == {"fillfactor": "70"}


def test_event_triggers():
    triggers = _hard_gaps()["root"]["event_triggers"]
    assert "audit_ddl" in triggers
    assert triggers["audit_ddl"]["attributes"]["event"] == "ddl_command_end"


# --- 4. optional names (owner decision "c") ---------------------------------

NAMED_JSON = {
    "db": "nm",
    "schemas": [
        {
            "name": "s",
            "tables": [
                {"name": "author", "pkey": "id", "columns": [
                    {"name": "id", "dtype": "serial"}]},
                {
                    "name": "recipe", "pkey": "id", "columns": [
                        {"name": "id", "dtype": "serial"},
                        {"name": "title", "dtype": "A", "size": "0:80"},
                        {"name": "author_id", "dtype": "I"},
                    ],
                    "relations": [
                        {"columns": ["author_id"], "related_schema": "s",
                         "related_table": "author", "related_columns": ["id"],
                         "name": "fk_recipe_author"},
                    ],
                    "constraints": [
                        {"type": "UNIQUE", "columns": ["title"], "name": "uq_title"},
                    ],
                    "indexes": [
                        {"columns": ["title"], "name": "idx_title"},
                    ],
                },
            ],
        },
    ],
}

ANON_JSON = {
    "db": "an",
    "schemas": [
        {
            "name": "s",
            "tables": [
                {"name": "author", "pkey": "id", "columns": [
                    {"name": "id", "dtype": "serial"}]},
                {
                    "name": "recipe", "pkey": "id", "columns": [
                        {"name": "id", "dtype": "serial"},
                        {"name": "author_id", "dtype": "I"},
                    ],
                    "relations": [
                        {"columns": ["author_id"], "related_schema": "s",
                         "related_table": "author", "related_columns": ["id"]},
                    ],
                },
            ],
        },
    ],
}


def test_named_entities_carry_readable_name_key_stays_hash():
    recipe = (JsonStructureProducer(NAMED_JSON).get_json_struct()
              ["root"]["schemas"]["s"]["tables"]["recipe"])

    (rel_key, rel), = recipe["relations"].items()
    assert rel["attributes"]["constraint_name"] == "fk_recipe_author"
    assert rel_key == rel["entity_name"] == hashed_name(
        schema="s", table="recipe", columns=["author_id"], obj_type="fk")

    (cst_key, cst), = recipe["constraints"].items()
    assert cst["attributes"]["constraint_name"] == "uq_title"
    assert cst_key == cst["entity_name"] == hashed_name(
        schema="s", table="recipe", columns=["title"], obj_type="cst")

    (idx_key, idx), = recipe["indexes"].items()
    assert idx["attributes"]["index_name"] == "idx_title"
    assert idx_key == idx["entity_name"] == hashed_name(
        schema="s", table="recipe", columns=["title"], obj_type="idx")


def test_anonymous_relation_name_is_the_hash():
    recipe = (JsonStructureProducer(ANON_JSON).get_json_struct()
              ["root"]["schemas"]["s"]["tables"]["recipe"])
    (rel_key, rel), = recipe["relations"].items()
    expected = hashed_name(schema="s", table="recipe",
                           columns=["author_id"], obj_type="fk")
    assert rel["attributes"]["constraint_name"] == expected == rel_key


# --- 5. validation round-trip -----------------------------------------------

def test_produced_structure_validates():
    StructureValidator().validate(
        JsonStructureProducer(EQUIVALENT_JSON).get_json_struct())
    StructureValidator().validate(_hard_gaps())
    StructureValidator().validate(
        JsonStructureProducer(NAMED_JSON).get_json_struct())


# --- 6. idempotence ---------------------------------------------------------

def test_idempotence():
    a = JsonStructureProducer(EQUIVALENT_JSON).get_json_struct()
    b = JsonStructureProducer(EQUIVALENT_JSON).get_json_struct()
    assert json_equal(a, b)


# --- constructor / from JSON string -----------------------------------------

def test_accepts_json_string():
    import json as _json
    from_str = JsonStructureProducer(_json.dumps(MINIMAL_JSON)).get_json_struct()
    assert json_equal(from_str, _minimal())


def test_rejects_bad_root():
    import pytest
    with pytest.raises(ValueError):
        JsonStructureProducer({"database": "x"})
