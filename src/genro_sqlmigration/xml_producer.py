# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""
xml_producer.py - normalized JSON from a clean SQL-model XML
============================================================

Reads an XML document conforming to ``schemas/sql_model-1.0.xsd`` (the
natural, GUI-friendly form of the physical plane) and projects it into the
normalized migrator JSON, reusing the :mod:`structures` factories. This is
the producer for those who describe a database in XML — e.g. an XSD-driven
editor — instead of through an ORM.

Normalization rules applied (producer-guide, physical plane):

- pkey columns get ``notnull='_auto_'``; a single-column PK drops a
  redundant ``unique``;
- an ``indexed`` column generates an index (``indexed`` is not a column
  attribute in the contract, so it never reaches the column JSON);
- FK / UNIQUE / index names are the structural hashes computed by the
  factories; boolean attributes arrive as XML strings and are coerced.

Not yet (raises or skipped, noted for later): FK supporting-index defaults;
``related_columns`` defaulting to the target pkey when ``to`` omits the
column (a two-part ``to`` raises); ``size`` min:max re-normalization.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

from .structures import (
    COL_JSON_KEYS,
    new_column_item,
    new_constraint_item,
    new_extension_item,
    new_index_item,
    new_relation_item,
    new_schema_item,
    new_structure_root,
    new_table_item,
)

NS = "urn:genro:sql-model:1.0"


def _tag(elem):
    """Local tag name, namespace stripped."""
    return elem.tag.split("}")[-1]


def _bool(value):
    """Coerce an XML boolean lexical value to a Python bool."""
    return value is not None and value.lower() in ("true", "1")


class XmlStructureProducer:
    """Project a clean SQL-model XML into the normalized migrator JSON."""

    def __init__(self, xml_text):
        self.root = ET.fromstring(xml_text)
        if _tag(self.root) != "db":
            raise ValueError(f"root element must be <db>, got <{_tag(self.root)}>")

    @classmethod
    def from_file(cls, path):
        """Build a producer from an XML file path."""
        return cls(Path(path).read_text(encoding="utf-8"))

    def get_json_struct(self):
        """Build the complete normalized JSON structure from the XML."""
        structure = new_structure_root(self.root.get("name"))
        root = structure["root"]
        for elem in self.root:
            tag = _tag(elem)
            if tag == "schema":
                self._fill_schema(root, elem)
            elif tag == "extension":
                name = elem.get("name")
                root["extensions"][name] = new_extension_item(name)
        return structure

    def _fill_schema(self, root, schema_elem):
        schema_name = schema_elem.get("name")
        root["schemas"][schema_name] = new_schema_item(schema_name)
        for table_elem in schema_elem:
            if _tag(table_elem) == "table":
                self._fill_table(root, schema_name, table_elem)

    def _fill_table(self, root, schema_name, table_elem):
        table_name = table_elem.get("name")
        table = new_table_item(schema_name, table_name)
        pkey = table_elem.get("pkey")
        if pkey:
            table["attributes"]["pkeys"] = pkey
        if table_elem.get("comment"):
            table["attributes"]["comment"] = table_elem.get("comment")
        root["schemas"][schema_name]["tables"][table_name] = table

        pkey_cols = pkey.split(",") if pkey else []
        for child in table_elem:
            tag = _tag(child)
            if tag == "column":
                self._fill_column(table, schema_name, table_name, child, pkey_cols)
            elif tag == "constraint":
                self._fill_constraint(table, schema_name, table_name, child)
            elif tag == "index":
                cols = child.get("columns").split(",")
                self._add_index(table, schema_name, table_name, cols, child)

    def _fill_column(self, table, schema_name, table_name, col_elem, pkey_cols):
        name = col_elem.get("name")
        attrs = {k: col_elem.get(k) for k in COL_JSON_KEYS
                 if col_elem.get(k) is not None}
        if "notnull" in attrs:
            attrs["notnull"] = _bool(attrs["notnull"])
        if "unique" in attrs:
            attrs["unique"] = _bool(attrs["unique"])
        if "dtype" not in attrs:  # producer-guide default
            attrs["dtype"] = "A" if attrs.get("size") else "T"
        if name in pkey_cols:
            attrs["notnull"] = "_auto_"
            if len(pkey_cols) == 1:
                attrs.pop("unique", None)
        table["columns"][name] = new_column_item(
            schema_name, table_name, name, attributes=attrs
        )

        relation = next((c for c in col_elem if _tag(c) == "relation"), None)
        if relation is not None:
            self._add_relation(table, schema_name, table_name, [name], relation)
        # 'indexed' is a flag, not a column attribute: it generates an index.
        if _bool(col_elem.get("indexed")) and name not in pkey_cols:
            self._add_index(table, schema_name, table_name, [name])

    def _add_relation(self, table, schema_name, table_name, columns, rel_elem):
        parts = rel_elem.get("to").split(".")
        if len(parts) != 3:
            raise NotImplementedError(
                f"relation 'to={rel_elem.get('to')}': target must be "
                "schema.table.column (pkey defaulting not yet implemented)"
            )
        related_schema, related_table, related_column = parts
        attrs = {
            "related_schema": related_schema,
            "related_table": related_table,
            "related_columns": [related_column],
            "constraint_type": "FOREIGN KEY",
        }
        for key in ("on_delete", "on_update"):
            if rel_elem.get(key):
                attrs[key] = rel_elem.get(key)
        for key in ("deferrable", "initially_deferred"):
            if _bool(rel_elem.get(key)):
                attrs[key] = True
        item = new_relation_item(schema_name, table_name, columns, attributes=attrs)
        table["relations"][item["entity_name"]] = item

    def _fill_constraint(self, table, schema_name, table_name, elem):
        if elem.get("type") == "CHECK":
            item = new_constraint_item(
                schema_name, table_name, None, "CHECK",
                constraint_name=elem.get("name"),
                check_clause=elem.get("check_clause"),
            )
        else:
            item = new_constraint_item(
                schema_name, table_name, elem.get("columns").split(","), "UNIQUE"
            )
        table["constraints"][item["entity_name"]] = item

    def _add_index(self, table, schema_name, table_name, columns, elem=None):
        attrs = {"columns": dict.fromkeys(columns)}
        if elem is not None:
            if _bool(elem.get("unique")):
                attrs["unique"] = True
            for key in ("method", "where", "tablespace"):
                if elem.get(key):
                    attrs[key] = elem.get(key)
        item = new_index_item(schema_name, table_name, columns, attributes=attrs)
        table["indexes"][item["entity_name"]] = item


def struct_to_xml(structure, indent=True):
    """Inverse of :class:`XmlStructureProducer`: normalized JSON → natural XML.

    De-normalizes so the XML can go back into the editor: a relation's source
    column hosts a ``<relation to="schema.table.column"/>``; hashed names
    (``fk_``/``cst_``/``idx_``) are dropped from the natural form (the producer
    regenerates them), so ``XML → JSON → XML → JSON`` round-trips to the same
    JSON. Feeds the round-trip: a DB introspected to JSON becomes editable XML.

    Not yet: multi-column relations (the clean XSD models one relation per
    single column) are skipped — noted for the compositeColumn extension.
    """
    root = structure["root"]
    ET.register_namespace("", NS)
    db = ET.Element(f"{{{NS}}}db", {"name": root.get("entity_name", "")})
    for schema_name, schema in root.get("schemas", {}).items():
        s_el = ET.SubElement(db, f"{{{NS}}}schema", {"name": schema_name})
        for table_name, table in schema.get("tables", {}).items():
            t_attrs = {"name": table_name}
            table_attributes = table.get("attributes", {})
            if table_attributes.get("pkeys"):
                t_attrs["pkey"] = table_attributes["pkeys"]
            if table_attributes.get("comment"):
                t_attrs["comment"] = table_attributes["comment"]
            t_el = ET.SubElement(s_el, f"{{{NS}}}table", t_attrs)

            rel_by_col = {}
            for rel in table.get("relations", {}).values():
                cols = rel["attributes"].get("columns") or []
                if len(cols) == 1:
                    rel_by_col[cols[0]] = rel["attributes"]

            for col_name, col in table.get("columns", {}).items():
                c_attrs = {"name": col_name}
                for key, value in col.get("attributes", {}).items():
                    if key not in COL_JSON_KEYS:
                        continue
                    if value is True or value == "_auto_":
                        c_attrs[key if value is True else "notnull"] = "true"
                    else:
                        c_attrs[key] = str(value)
                c_el = ET.SubElement(t_el, f"{{{NS}}}column", c_attrs)
                rel = rel_by_col.get(col_name)
                if rel:
                    r_attrs = {"to": (f"{rel['related_schema']}."
                                      f"{rel['related_table']}."
                                      f"{rel['related_columns'][0]}")}
                    for key in ("on_delete", "on_update"):
                        if rel.get(key):
                            r_attrs[key] = rel[key]
                    ET.SubElement(c_el, f"{{{NS}}}relation", r_attrs)

            for constraint in table.get("constraints", {}).values():
                a = constraint["attributes"]
                c_attrs = {"name": constraint["entity_name"],
                           "type": a["constraint_type"]}
                if a["constraint_type"] == "CHECK":
                    c_attrs["check_clause"] = a["check_clause"]
                else:
                    c_attrs["columns"] = ",".join(a.get("columns") or [])
                ET.SubElement(t_el, f"{{{NS}}}constraint", c_attrs)

            for index in table.get("indexes", {}).values():
                a = index["attributes"]
                i_attrs = {"name": index["entity_name"],
                           "columns": ",".join((a.get("columns") or {}).keys())}
                if a.get("unique"):
                    i_attrs["unique"] = "true"
                if a.get("method"):
                    i_attrs["method"] = a["method"]
                ET.SubElement(t_el, f"{{{NS}}}index", i_attrs)

    for ext_name in root.get("extensions", {}):
        ET.SubElement(db, f"{{{NS}}}extension", {"name": ext_name})

    if indent:
        ET.indent(db)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(db, encoding="unicode")
