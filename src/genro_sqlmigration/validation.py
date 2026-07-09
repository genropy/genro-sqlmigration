"""
validation.py - Contract validation at the package boundary
=============================================================

Validates a producer-built normalized JSON structure against the
contract (format version 1.0) before it is injected into the migrator.

The validation has three layers:

1. **Normalization**: missing containers are filled with empty dicts
   (``schemas``, ``extensions``, ``event_triggers`` at root level;
   ``tables`` per schema; ``columns``/``relations``/``constraints``/
   ``indexes`` per table). This is the compatibility story between
   format versions: older producers simply lack newer containers.

2. **Formal validation** (optional): if ``jsonschema`` is installed
   (extra ``validation``), the structure is checked against the
   packaged JSON Schema ``schemas/structure-1.0.json``.

3. **Semantic validation** (always): referential rules a schema cannot
   express — pkey columns exist, constraint/index/FK columns exist,
   FK targets resolve inside the structure, structural hashed names
   (``cst_*``/``fk_*``/``idx_*``) are consistent with their content,
   dtype codes belong to the closed set.

Usage::

    from genro_sqlmigration import StructureValidator

    structure = my_producer.get_json_struct()
    migrator.ormStructure = StructureValidator().validate(structure)

``validate()`` returns a normalized deep copy (without the optional
``format_version`` envelope key) or raises :class:`SqlValidationError`
carrying the full list of problems.
"""

import copy
import json
from importlib.resources import files

try:
    import jsonschema
except ImportError:  # formal schema validation is an optional extra
    jsonschema = None

from .exceptions import SqlValidationError
from .structures import COL_JSON_KEYS, DTYPE_CODES, FORMAT_VERSION, hashed_name

ROOT_CONTAINERS = ('schemas', 'extensions', 'event_triggers')
TABLE_CONTAINERS = ('columns', 'relations', 'constraints', 'indexes')


class StructureValidator:
    """Validate and normalize a normalized-JSON structure.

    Args:
        format_version: Contract version accepted by this validator
            (default: the package's current ``FORMAT_VERSION``).
    """

    def __init__(self, format_version=FORMAT_VERSION):
        self.format_version = format_version
        self.errors = []

    def validate(self, structure):
        """Validate ``structure`` and return its normalized deep copy.

        Args:
            structure: Producer-built dict, ``{'root': {...}}`` with an
                optional top-level ``format_version``.

        Returns:
            dict: Normalized structure (``format_version`` stripped).

        Raises:
            SqlValidationError: With the full list of problems found.
        """
        self.errors = []
        if not isinstance(structure, dict) or 'root' not in structure:
            raise SqlValidationError(["structure must be a dict with a 'root' key"])

        declared_version = structure.get('format_version')
        if declared_version is not None and declared_version != self.format_version:
            self.errors.append(
                f"unsupported format_version '{declared_version}' "
                f"(expected '{self.format_version}')"
            )

        normalized = {'root': copy.deepcopy(structure['root'])}
        self.normalize_containers(normalized['root'])
        self.check_formal_schema(normalized)
        self.check_root(normalized['root'])
        if self.errors:
            raise SqlValidationError(self.errors)
        return normalized

    def normalize_containers(self, root):
        """Fill missing containers so older-format input diffs cleanly."""
        if not isinstance(root, dict):
            return
        for container in ROOT_CONTAINERS:
            root.setdefault(container, {})
        for schema_item in root['schemas'].values():
            if not isinstance(schema_item, dict):
                continue
            schema_item.setdefault('tables', {})
            for table_item in schema_item['tables'].values():
                if not isinstance(table_item, dict):
                    continue
                table_item.setdefault('attributes', {})
                for container in TABLE_CONTAINERS:
                    table_item.setdefault(container, {})

    def check_formal_schema(self, normalized):
        """Validate against the packaged JSON Schema when available."""
        if jsonschema is None:
            return
        schema_path = files('genro_sqlmigration') / 'schemas' / 'structure-1.0.json'
        schema = json.loads(schema_path.read_text())
        validator = jsonschema.Draft202012Validator(schema)
        for error in validator.iter_errors(normalized):
            path = '.'.join(str(p) for p in error.absolute_path) or '<root>'
            self.errors.append(f"schema: {path}: {error.message}")

    def check_root(self, root):
        self._current_root = root
        if root.get('entity') != 'db':
            self.errors.append("root: entity must be 'db'")
        if not root.get('entity_name'):
            self.errors.append("root: entity_name (database name) is required")
        for schema_name, schema_item in root['schemas'].items():
            self.check_schema(schema_name, schema_item)

    @property
    def current_root(self):
        """Root being validated (set by :meth:`check_root`)."""
        return self._current_root

    def check_schema(self, schema_name, schema_item):
        where = f"schemas.{schema_name}"
        if not isinstance(schema_item, dict):
            self.errors.append(f"{where}: must be a dict")
            return
        if schema_item.get('entity_name') != schema_name:
            self.errors.append(f"{where}: entity_name must equal its key")
        for table_name, table_item in schema_item['tables'].items():
            self.check_table(schema_name, table_name, table_item)

    def check_table(self, schema_name, table_name, table_item):
        where = f"schemas.{schema_name}.tables.{table_name}"
        if not isinstance(table_item, dict):
            self.errors.append(f"{where}: must be a dict")
            return
        columns = table_item['columns']
        pkeys = table_item['attributes'].get('pkeys')
        if pkeys:
            for pkey_column in pkeys.split(','):
                if pkey_column not in columns:
                    self.errors.append(
                        f"{where}: pkey column '{pkey_column}' is not a table column"
                    )
        for column_name, column_item in columns.items():
            self.check_column(where, column_name, column_item)
        for constraint_item in table_item['constraints'].values():
            self.check_constraint(where, schema_name, table_name,
                                  columns, constraint_item)
        for relation_item in table_item['relations'].values():
            self.check_relation(where, schema_name, table_name,
                                columns, relation_item)
        for index_item in table_item['indexes'].values():
            self.check_index(where, schema_name, table_name,
                             columns, index_item)

    def check_column(self, table_where, column_name, column_item):
        where = f"{table_where}.columns.{column_name}"
        attributes = column_item.get('attributes', {})
        for key in attributes:
            if key not in COL_JSON_KEYS:
                self.errors.append(f"{where}: unknown attribute '{key}'")
        dtype = attributes.get('dtype')
        if dtype is not None and dtype not in DTYPE_CODES:
            self.errors.append(f"{where}: unknown dtype '{dtype}'")

    def check_constraint(self, table_where, schema_name, table_name,
                         columns, constraint_item):
        entity_name = constraint_item.get('entity_name', '?')
        where = f"{table_where}.constraints.{entity_name}"
        attributes = constraint_item.get('attributes', {})
        constraint_type = attributes.get('constraint_type')
        if constraint_type == 'CHECK':
            if not attributes.get('check_clause'):
                self.errors.append(f"{where}: CHECK requires a check_clause")
            if attributes.get('constraint_name') != entity_name:
                self.errors.append(
                    f"{where}: CHECK entity_name must equal its constraint_name"
                )
            return
        constraint_columns = attributes.get('columns') or []
        for column_name in constraint_columns:
            if column_name not in columns:
                self.errors.append(
                    f"{where}: column '{column_name}' is not a table column"
                )
        expected = hashed_name(schema=schema_name, table=table_name,
                               columns=constraint_columns, obj_type='cst')
        if entity_name != expected:
            self.errors.append(
                f"{where}: entity_name is not the structural hash "
                f"'{expected}' of its columns"
            )

    def check_relation(self, table_where, schema_name, table_name,
                       columns, relation_item):
        entity_name = relation_item.get('entity_name', '?')
        where = f"{table_where}.relations.{entity_name}"
        attributes = relation_item.get('attributes', {})
        relation_columns = attributes.get('columns') or []
        for column_name in relation_columns:
            if column_name not in columns:
                self.errors.append(
                    f"{where}: column '{column_name}' is not a table column"
                )
        expected = hashed_name(schema=schema_name, table=table_name,
                               columns=relation_columns, obj_type='fk')
        if entity_name != expected:
            self.errors.append(
                f"{where}: entity_name is not the structural hash "
                f"'{expected}' of its columns"
            )
        self.check_relation_target(where, attributes)

    def check_relation_target(self, where, attributes):
        related_schema = attributes.get('related_schema')
        related_table = attributes.get('related_table')
        related_columns = attributes.get('related_columns') or []
        schemas = self.current_root['schemas']
        target_schema = schemas.get(related_schema)
        if target_schema is None:
            self.errors.append(
                f"{where}: related_schema '{related_schema}' not in structure"
            )
            return
        target_table = target_schema['tables'].get(related_table)
        if target_table is None:
            self.errors.append(
                f"{where}: related_table '{related_schema}.{related_table}' "
                f"not in structure"
            )
            return
        for column_name in related_columns:
            if column_name not in target_table['columns']:
                self.errors.append(
                    f"{where}: related column '{column_name}' not in "
                    f"'{related_schema}.{related_table}'"
                )

    def check_index(self, table_where, schema_name, table_name,
                    columns, index_item):
        entity_name = index_item.get('entity_name', '?')
        where = f"{table_where}.indexes.{entity_name}"
        attributes = index_item.get('attributes', {})
        index_columns = list((attributes.get('columns') or {}).keys())
        for column_name in index_columns:
            if column_name not in columns:
                self.errors.append(
                    f"{where}: column '{column_name}' is not a table column"
                )
        expected = hashed_name(schema=schema_name, table=table_name,
                               columns=index_columns, obj_type='idx')
        if entity_name != expected:
            self.errors.append(
                f"{where}: entity_name is not the structural hash "
                f"'{expected}' of its columns"
            )
