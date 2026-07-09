# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""
base_reader.py - Base interface for database readers
=====================================================

Single home of database introspection: a concrete template method drives
a per-dialect fetch phase and dialect-agnostic ``process_*`` phase, both
producing the normalized JSON structure defined in :mod:`structures`.
Concrete readers implement only the per-dialect ``connect``/``close``,
``fetch_*`` and ``is_empty_column`` hooks.
"""

from abc import ABC, abstractmethod

from genro_sqlmigration.exceptions import NonExistingDbException
from genro_sqlmigration.structures import (
    COL_JSON_KEYS,
    clean_attributes,
    nested_defaultdict,
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


class BaseReader(ABC):
    """Base interface for database introspection readers.

    Declares the introspection flow once: the concrete
    :meth:`get_json_struct` template connects, fetches raw data via the
    per-dialect ``fetch_*`` hooks and processes it into the normalized JSON
    structure with the shared ``process_*`` methods.

    Args:
        connection_params: Database-specific connection parameters. The
            format depends on the concrete implementation.
    """

    col_json_keys = COL_JSON_KEYS

    def __init__(self, connection_params=None):
        self.connection_params = connection_params

    def get_json_struct(self, dbname, schemas=None):
        """Read the database structure and return the normalized JSON.

        Returns ``{}`` if the database does not exist; a
        :class:`SqlConnectionException` propagates to the caller (it is not
        a missing database and must not trigger a CREATE DATABASE).
        """
        self.json_structure = new_structure_root(dbname)
        self.json_meta = nested_defaultdict()
        self.json_schemas = self.json_structure['root']['schemas']
        fetched = {}
        try:
            self.connect()
            if schemas:
                fetched['base_structure'] = self.fetch_base_structure(schemas)
                fetched['constraints'] = self.fetch_constraints(schemas)
                fetched['indexes'] = self.fetch_indexes(schemas)
                fetched['extensions'] = self.fetch_extensions()
                fetched['event_triggers'] = self.fetch_event_triggers()
        except NonExistingDbException:
            return {}
        finally:
            self.close()
        for key, data in fetched.items():
            getattr(self, f'process_{key}')(data, schemas=schemas)
        return self.json_structure

    # -- Per-dialect hooks ----------------------------------------------------

    @abstractmethod
    def connect(self):
        """Open the database connection used for introspection."""
        ...

    @abstractmethod
    def close(self):
        """Close the database connection if open."""
        ...

    @abstractmethod
    def fetch_base_structure(self, schemas):
        """Return schema/table/column rows in the legacy normalized format
        consumed by :meth:`process_base_structure`."""
        ...

    @abstractmethod
    def fetch_constraints(self, schemas):
        """Return constraint rows in the legacy normalized format consumed
        by :meth:`process_constraints`."""
        ...

    @abstractmethod
    def fetch_indexes(self, schemas):
        """Return index rows in the legacy normalized format consumed by
        :meth:`process_indexes`."""
        ...

    @abstractmethod
    def fetch_extensions(self):
        """Return extension rows in the legacy normalized format consumed
        by :meth:`process_extensions`."""
        ...

    @abstractmethod
    def fetch_event_triggers(self):
        """Return event-trigger rows in the legacy normalized format
        consumed by :meth:`process_event_triggers`."""
        ...

    @abstractmethod
    def is_empty_column(self, schema_name, table_name, column_name):
        """Return True if the column contains no non-NULL values.

        Used before type conversions to determine whether it is safe to
        proceed without risk of data loss.
        """
        ...

    # -- Shared processing ----------------------------------------------------

    def process_base_structure(self, base_structure, schemas=None):
        """Process base information: schemas, tables and columns.

        Column metadata carries ``_pg_``-prefixed helper fields; the
        remaining fields are attributes filtered via ``COL_JSON_KEYS``.
        Schemas present in ``schemas`` but empty in the DB are removed.
        """
        # Pre-initialize all schemas to None to track empty ones
        for schema_name in schemas:
            self.json_schemas[schema_name] = None

        for c in base_structure:
            schema_name = c.pop('_pg_schema_name')
            table_name = c.pop('_pg_table_name')
            is_nullable = c.pop('_pg_is_nullable')
            column_name = c.pop('name')
            table_comment = c.pop('_pg_table_comment', None)
            colattr = {
                k: v for k, v in c.items()
                if k in self.col_json_keys and v is not None
            }
            if not self.json_schemas[schema_name]:
                self.json_schemas[schema_name] = new_schema_item(schema_name)
            if table_name and table_name not in self.json_schemas[schema_name]["tables"]:
                self.json_schemas[schema_name]["tables"][table_name] = (
                    new_table_item(schema_name, table_name)
                )
            if table_name and table_comment:
                self.json_schemas[schema_name]["tables"][table_name][
                    'attributes']['comment'] = table_comment
            if column_name:
                if is_nullable == 'NO':
                    colattr['notnull'] = True
                col_item = new_column_item(
                    schema_name, table_name, column_name, attributes=colattr
                )
                self.json_schemas[schema_name]["tables"][table_name]["columns"][column_name] = col_item

        # Remove schemas that exist in the list but are empty in the DB
        for schema_name in schemas:
            if not self.json_schemas[schema_name]:
                self.json_schemas.pop(schema_name)

    def process_constraints(self, constraints_dict, schemas=None):
        """Process all constraints extracted from the database.

        PRIMARY KEY sets ``pkeys`` and marks PK columns auto-notnull;
        single-column UNIQUE becomes a column attribute, multi-column UNIQUE
        stays a separate constraint; FOREIGN KEY is delegated to
        :meth:`process_table_relations`; CHECK becomes a named constraint.
        """
        for tablepath, constraints_by_type in constraints_dict.items():
            schema_name, table_name = tablepath
            d = dict(constraints_by_type)
            table_json = self.json_schemas[schema_name]["tables"][table_name]

            # PRIMARY KEY: set pkeys and mark columns as auto-notnull
            primary_key_const = d.pop("PRIMARY KEY", {})
            if primary_key_const:
                pkeys = primary_key_const["columns"]
                table_json['attributes']['pkeys'] = ','.join(pkeys)
                for col in pkeys:
                    table_json['columns'][col]['attributes']['notnull'] = '_auto_'

            # UNIQUE: single column -> attribute, multi-column -> constraint
            unique = d.pop("UNIQUE", {})
            multiple_unique = dict(unique)
            for k, v in unique.items():
                columns = v['columns']
                if len(columns) == 1:
                    # UNIQUE on single column that coincides with pkey -> ignored
                    if columns[0] == table_json['attributes']['pkeys']:
                        continue
                    # UNIQUE on single column -> column attribute
                    multiple_unique.pop(k)
                    self.json_schemas[schema_name]["tables"][table_name][
                        'columns'][columns[0]]['attributes']['unique'] = True

            # FOREIGN KEY -> delegate to process_table_relations
            self.process_table_relations(
                schema_name, table_name, d.pop('FOREIGN KEY', {})
            )

            # CHECK: name-keyed, clause-based constraints
            for check_name, check_const in d.pop('CHECK', {}).items():
                const_item = new_constraint_item(
                    schema_name, table_name, None,
                    constraint_type='CHECK',
                    constraint_name=check_name,
                    check_clause=check_const.get('check_clause'),
                )
                table_json['constraints'][const_item['entity_name']] = const_item

            # Multi-column UNIQUE -> separate constraint
            for multiple_unique_const in multiple_unique.values():
                const_item = new_constraint_item(
                    schema_name, table_name,
                    multiple_unique_const['columns'],
                    constraint_type='UNIQUE',
                    constraint_name=multiple_unique_const['constraint_name']
                )
                table_json['constraints'][const_item['entity_name']] = const_item

    def process_table_relations(self, schema_name, table_name, foreign_keys_dict):
        """Process the foreign keys of a table.

        For each FK creates a relation item with hashed name and adds it to
        the table's ``relations`` section.
        """
        relations = self.json_schemas[schema_name]["tables"][table_name]['relations']
        for entity_attributes in foreign_keys_dict.values():
            constraint_name = entity_attributes.pop('constraint_name', None)
            relation_item = new_relation_item(
                schema_name, table_name,
                columns=entity_attributes['columns'],
                attributes=entity_attributes,
                constraint_name=constraint_name
            )
            relations[relation_item['entity_name']] = relation_item

    def process_indexes(self, indexes_dict, schemas=None):
        """Process indexes extracted from the database.

        Indexes with an associated ``constraint_type`` (created
        automatically by PK or UNIQUE) are skipped, being already
        represented by the corresponding constraint.
        """
        for tablepath, index_dict in indexes_dict.items():
            schema_name, table_name = tablepath
            d = dict(index_dict)
            table_json = self.json_schemas[schema_name]["tables"][table_name]
            for index_name, index_attributes in d.items():
                # Skip indexes automatically created by constraints (PK, UNIQUE)
                if index_attributes.get('constraint_type'):
                    continue
                indexed_columns = list(index_attributes['columns'].keys())
                index_item = new_index_item(
                    schema_name, table_name,
                    columns=indexed_columns,
                    attributes=index_attributes,
                    index_name=index_name
                )
                table_json['indexes'][index_item['entity_name']] = index_item

    def process_extensions(self, extensions, **kwargs):
        """Process installed PostgreSQL extensions.

        Extensions in the ``pg_catalog`` schema are always present and not
        relevant for migration, so they are skipped.
        """
        for extension_name, extension_dict in extensions.items():
            if extension_dict.get('schema_name') == 'pg_catalog':
                continue
            extension_dict = clean_attributes(extension_dict)
            extension_item = new_extension_item(extension_name)
            self.json_meta['root']['extension'] = extension_dict
            self.json_structure["root"]['extensions'][extension_name] = extension_item

    def process_event_triggers(self, event_triggers, **kwargs):
        """Process DDL event triggers of the database.

        Event triggers respond to database-level DDL events such as CREATE,
        ALTER or DROP TABLE.
        """
        for event_trigger_name, event_trigger_dict in event_triggers.items():
            event_trigger_item = new_event_trigger_item(event_trigger_name)
            event_trigger_item['attributes'].update(event_trigger_dict)
            self.json_structure["root"]['event_triggers'][event_trigger_name] = event_trigger_item
