# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""
json_producer.py - normalized JSON from a clean SQL-model human JSON
====================================================================

The JSON twin of :mod:`xml_producer`. Reads the ergonomic, GUI-friendly
"human" form of the physical plane (a plain Python dict, or its JSON
string) and projects it into the normalized migrator JSON, reusing the
:mod:`structures` factories. Only the input parsing differs from the XML
producer (a dict instead of XML elements); the projection logic and the
normalization rules are identical, so the two external ports converge on
one internal form for equivalent models.

Normalization rules applied (shared with the XML producer):

- pkey columns get ``notnull='_auto_'``; a single-column PK drops a
  redundant ``unique``;
- FK / UNIQUE / index structural names are the hashes computed by the
  factories (this compiler never computes a hash itself);
- ``dtype`` default: absent -> ``'A'`` if ``size`` present else ``'T'``.

Hard gaps the XML 1.0 form cannot express yet, covered here:

- **multi-column FK / UNIQUE**: relation/constraint ``columns`` is a list
  of names, passed whole to the factory (which hashes the full list);
- **index sort order**: index ``columns`` is a map name->sortorder
  (``null`` = default, ``"DESC"`` = descending); a plain list is
  normalized to a map of nulls;
- **index ``with_options``**: an object passed through;
- **event triggers**: a top-level list, each with a name and optional
  attributes (via :func:`new_event_trigger_item`);
- **optional names**: an explicit ``name`` on a relation / UNIQUE / index
  becomes the entity's readable name (``constraint_name`` / ``index_name``);
  the dict key stays the factory's structural hash regardless.
"""

import json
from pathlib import Path

from .structures import (
    COL_JSON_KEYS,
    new_column_item,
    new_constraint_item,
    new_event_trigger_item,
    new_extension_item,
    new_index_item,
    new_relation_item,
    new_schema_item,
    new_structure_root,
    new_table_item,
)


class JsonStructureProducer:
    """Project a clean SQL-model human JSON into the normalized migrator JSON."""

    def __init__(self, source):
        self.source = json.loads(source) if isinstance(source, str) else source
        if not isinstance(self.source, dict) or "db" not in self.source:
            raise ValueError("source must be a dict (or JSON) with a 'db' key")

    @classmethod
    def from_file(cls, path):
        """Build a producer from a JSON file path."""
        return cls(Path(path).read_text(encoding="utf-8"))

    def get_json_struct(self):
        """Build the complete normalized JSON structure from the human JSON."""
        structure = new_structure_root(self.source.get("db"))
        root = structure["root"]
        for schema in self.source.get("schemas", []):
            self._fill_schema(root, schema)
        for extension_name in self.source.get("extensions", []):
            root["extensions"][extension_name] = new_extension_item(extension_name)
        for trigger in self.source.get("event_triggers", []):
            self._add_event_trigger(root, trigger)
        return structure

    def _add_event_trigger(self, root, trigger):
        name = trigger.get("name")
        item = new_event_trigger_item(name)
        item["attributes"].update(trigger.get("attributes") or {})
        root["event_triggers"][name] = item

    def _fill_schema(self, root, schema):
        schema_name = schema.get("name")
        root["schemas"][schema_name] = new_schema_item(schema_name)
        for table in schema.get("tables", []):
            self._fill_table(root, schema_name, table)

    def _fill_table(self, root, schema_name, table):
        table_name = table.get("name")
        table_item = new_table_item(schema_name, table_name)
        pkey = table.get("pkey")
        if pkey:
            table_item["attributes"]["pkeys"] = pkey
        if table.get("comment"):
            table_item["attributes"]["comment"] = table.get("comment")
        root["schemas"][schema_name]["tables"][table_name] = table_item

        pkey_cols = pkey.split(",") if pkey else []
        for column in table.get("columns", []):
            self._fill_column(table_item, schema_name, table_name, column, pkey_cols)
        for relation in table.get("relations", []):
            self._add_relation(table_item, schema_name, table_name, relation)
        for constraint in table.get("constraints", []):
            self._fill_constraint(table_item, schema_name, table_name, constraint)
        for index in table.get("indexes", []):
            self._add_index(table_item, schema_name, table_name, index)

    def _fill_column(self, table_item, schema_name, table_name, column, pkey_cols):
        name = column.get("name")
        attrs = {k: column[k] for k in COL_JSON_KEYS if column.get(k) is not None}
        if "dtype" not in attrs:  # producer-guide default
            attrs["dtype"] = "A" if attrs.get("size") else "T"
        if name in pkey_cols:
            attrs["notnull"] = "_auto_"
            if len(pkey_cols) == 1:
                attrs.pop("unique", None)
        table_item["columns"][name] = new_column_item(
            schema_name, table_name, name, attributes=attrs
        )

    def _add_relation(self, table_item, schema_name, table_name, relation):
        attrs = {
            "related_schema": relation.get("related_schema"),
            "related_table": relation.get("related_table"),
            "related_columns": relation.get("related_columns"),
            "constraint_type": "FOREIGN KEY",
        }
        for key in ("on_delete", "on_update"):
            if relation.get(key):
                attrs[key] = relation[key]
        for key in ("deferrable", "initially_deferred"):
            if relation.get(key):
                attrs[key] = True
        item = new_relation_item(
            schema_name, table_name, relation.get("columns"),
            attributes=attrs, constraint_name=relation.get("name"),
        )
        table_item["relations"][item["entity_name"]] = item

    def _fill_constraint(self, table_item, schema_name, table_name, constraint):
        if constraint.get("type") == "CHECK":
            item = new_constraint_item(
                schema_name, table_name, None, "CHECK",
                constraint_name=constraint.get("name"),
                check_clause=constraint.get("check_clause"),
            )
        else:
            item = new_constraint_item(
                schema_name, table_name, constraint.get("columns"), "UNIQUE",
                constraint_name=constraint.get("name"),
            )
        table_item["constraints"][item["entity_name"]] = item

    def _add_index(self, table_item, schema_name, table_name, index):
        columns = index.get("columns")
        if isinstance(columns, list):  # normalize a plain list to a map of nulls
            columns = dict.fromkeys(columns)
        attrs = {"columns": columns}
        if index.get("unique"):
            attrs["unique"] = True
        for key in ("method", "where", "tablespace", "with_options"):
            if index.get(key):
                attrs[key] = index[key]
        item = new_index_item(
            schema_name, table_name, list(columns.keys()),
            attributes=attrs, index_name=index.get("name"),
        )
        table_item["indexes"][item["entity_name"]] = item
