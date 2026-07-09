"""Test-side ORM model and JSON producer.

The legacy oracle suite builds fixtures through the Genropy ORM
(``db.model.src.package(...).table(...).column(...)``) and lets the legacy
``orm_extractor`` project them into the normalized JSON. The package has no
ORM by design, so this module provides:

- a tiny model (``SrcModel``/``SrcPackage``/``SrcTable``/``SrcColumn``)
  mirroring the fixture-facing surface of the legacy ORM;
- ``OrmJsonProducer``, a faithful port of the legacy
  ``gnrsqlmigration/orm_extractor.py`` projection rules over that model.

This is the first real exercise of the producer API: every rule encoded here
(dtype defaults and normalization, pkey handling, joiner defaults, deferred
indexes, per-dtype index config) is producer-guide material for M2
(roadmap doc ``04`` §2).
"""

from genro_sqlmigration.structures import (
    COL_JSON_KEYS,
    camel_to_snake,
    hashed_name,
    new_column_item,
    new_constraint_item,
    new_extension_item,
    new_index_item,
    new_relation_item,
    new_schema_item,
    new_structure_root,
    new_table_item,
)

GNR_DTYPE_CONVERTER = {'X': 'T', 'Z': 'T', 'P': 'T'}
"""Genropy-internal dtypes normalized to text (producer responsibility)."""

DTYPE_INDEX_CONFIG = {
    'TSV': {'method': 'gin', 'required': True},
}
"""Per-dtype index defaults (#629): TSV columns always get a GIN index."""

AUTO_EXTENSION_ATTRIBUTES = ['unaccent']


# ---------------------------------------------------------------------------
# Model source objects (fixture-facing surface of the legacy ORM)
# ---------------------------------------------------------------------------

class SrcModel:
    """Root of the test model: a dict of packages, one per SQL schema."""

    def __init__(self):
        self.packages = {}

    def package(self, name, sqlschema=None, **attributes):
        pkg = self.packages.get(name)
        if pkg is None:
            pkg = SrcPackage(self, name, sqlschema=sqlschema, **attributes)
            self.packages[name] = pkg
        return pkg

    def schema_names(self):
        return [pkg.sqlname for pkg in self.packages.values()]

    def table(self, path):
        """Resolve ``pkg.table`` into the SrcTable object."""
        pkg_name, table_name = path.split('.')
        return self.packages[pkg_name].tables[table_name]


class SrcPackage:
    """A package: corresponds to one SQL schema."""

    def __init__(self, model, name, sqlschema=None, **attributes):
        self.model = model
        self.name = name
        self.sqlname = sqlschema or name
        self.attributes = dict(attributes)
        self.tables = {}

    def table(self, name, pkey=None, **attributes):
        tbl = self.tables.get(name)
        if tbl is None:
            tbl = SrcTable(self, name)
            self.tables[name] = tbl
        if pkey is not None:
            tbl.attributes['pkey'] = pkey
        tbl.attributes.update(
            {k: v for k, v in attributes.items() if v is not None}
        )
        return tbl


class SrcTable:
    """A table: columns, composite columns and a ``pkey`` attribute."""

    def __init__(self, package, name):
        self.package = package
        self.name = name
        self.attributes = {}
        self.columns = {}
        self.composite_columns = {}
        self.check_constraints = {}

    @property
    def sqlname(self):
        return f'{self.package.name}_{self.name}'

    @property
    def pkeys(self):
        """Physical primary-key column names (composite names expanded)."""
        pkey = self.attributes.get('pkey')
        if not pkey:
            return []
        expanded = []
        for name in pkey.split(','):
            composite = self.composite_columns.get(name)
            if composite is not None:
                expanded.extend(composite.attributes['composed_of'].split(','))
            else:
                expanded.append(name)
        return expanded

    def column(self, name, dtype=None, size=None, **attributes):
        col = self.columns.get(name)
        if col is None:
            col = SrcColumn(self, name)
            self.columns[name] = col
        if dtype is not None:
            col.attributes['dtype'] = dtype
        if size is not None:
            col.attributes['size'] = size
        col.attributes.update(
            {k: v for k, v in attributes.items() if v is not None}
        )
        # Legacy compiled-column default: 'A' with a size, 'T' otherwise
        if not col.attributes.get('dtype'):
            col.attributes['dtype'] = 'A' if col.attributes.get('size') else 'T'
        return col

    def compositeColumn(self, name, columns=None, **attributes):
        col = self.composite_columns.get(name)
        if col is None:
            col = SrcColumn(self, name)
            self.composite_columns[name] = col
        if columns is not None:
            col.attributes['composed_of'] = columns
        col.attributes.update(
            {k: v for k, v in attributes.items() if v is not None}
        )
        return col

    def checkConstraint(self, name, check_clause):
        """Declare a named CHECK constraint.

        The clause must be written as PostgreSQL prints it (e.g.
        ``(rating >= 0)``) to compare clean against introspection.
        """
        self.check_constraints[name] = check_clause

    def __getitem__(self, key):
        if key == 'columns':
            return self.columns
        raise KeyError(key)


class SrcColumn:
    """A (possibly composite) column with an optional outgoing relation."""

    def __init__(self, table, name):
        self.table = table
        self.name = name
        self.attributes = {}
        self.joiner = None

    @property
    def sqlname(self):
        return self.name

    def relation(self, related_column, mode='relation',
                 onDelete=None, onDelete_sql=None,
                 onUpdate=None, onUpdate_sql='cascade',
                 deferred=None, deferrable=None, **kwargs):
        """Declare a relation; defaults ported from the legacy model.

        ``onUpdate_sql`` defaults to ``'cascade'`` and ``deferred`` is
        implied by ``setnull`` delete actions, exactly as in the legacy
        ``model.py`` relation handling.
        """
        if deferred is None and (onDelete == 'setnull' or onDelete_sql == 'setnull'):
            deferred = True
        parts = related_column.split('.')
        if len(parts) == 2:
            parts.insert(0, self.table.package.name)
        self.joiner = {
            'one_relation': '.'.join(parts),
            'foreignkey': mode == 'foreignkey',
            'onDelete_sql': onDelete_sql,
            'onUpdate_sql': onUpdate_sql,
            'deferred': deferred,
            'deferrable': deferrable,
        }
        return self

    def relatedColumnJoiner(self):
        return self.joiner


# ---------------------------------------------------------------------------
# Producer: model -> normalized JSON (port of the legacy orm_extractor)
# ---------------------------------------------------------------------------

class OrmJsonProducer:
    """Project a :class:`SrcModel` into the normalized migration JSON."""

    col_json_keys = COL_JSON_KEYS

    def __init__(self, model, dbname):
        self.model = model
        self.dbname = dbname

    def get_json_struct(self, extensions=None):
        """Build the complete JSON structure from the model."""
        self.json_structure = new_structure_root(self.dbname)
        self.schemas = self.json_structure['root']['schemas']
        self.deferred_indexes = []
        self.extensions = list(extensions or [])
        for pkg in self.model.packages.values():
            self.fill_json_package(pkg)
        for deferred_kw in self.deferred_indexes:
            self.fill_json_column_index(colobj=deferred_kw['colobj'], indexed=True)
        for extension_name in self.extensions:
            self.json_structure['root']['extensions'][extension_name] = (
                new_extension_item(extension_name)
            )
        return self.json_structure

    def fill_json_package(self, pkgobj):
        schema_name = pkgobj.sqlname
        self.schemas[schema_name] = new_schema_item(schema_name)
        for tblobj in pkgobj.tables.values():
            self.fill_json_table(tblobj)

    def fill_json_table(self, tblobj):
        schema_name = tblobj.package.sqlname
        table_name = tblobj.sqlname
        pkeys = (
            ','.join([tblobj.columns[col].sqlname for col in tblobj.pkeys])
            if tblobj.pkeys else None
        )
        table_entity = new_table_item(schema_name, table_name)
        table_entity['attributes']['pkeys'] = pkeys
        if tblobj.attributes.get('comment'):
            table_entity['attributes']['comment'] = tblobj.attributes['comment']
        self.schemas[schema_name]['tables'][table_name] = table_entity

        for colobj in tblobj.columns.values():
            self.fill_json_column(colobj)
            self.fill_json_relations_and_indexes(colobj)

        for compositecol in tblobj.composite_columns.values():
            self.fill_json_relations_and_indexes(compositecol)
            self.fill_multiple_unique_constraint(compositecol)

        for check_name, check_clause in tblobj.check_constraints.items():
            check_item = new_constraint_item(
                schema_name, table_name, None, 'CHECK',
                constraint_name=check_name, check_clause=check_clause
            )
            table_entity['constraints'][check_item['entity_name']] = check_item

    def fill_json_column(self, colobj):
        table_name = colobj.table.sqlname
        schema_name = colobj.table.package.sqlname
        colattr = colobj.attributes

        for auto_ext_attribute in AUTO_EXTENSION_ATTRIBUTES:
            if colattr.get(auto_ext_attribute) and auto_ext_attribute not in self.extensions:
                self.extensions.append(auto_ext_attribute)

        attributes = self.convert_colattr(colattr)

        # Normalize min:max sizes forcing min to 0: the DB side cannot know
        # the min, keeping it would flag a change on every comparison.
        if ":" in attributes.get('size', '') and not attributes.get('size').startswith('0'):
            attributes['size'] = f"0:{attributes['size'].split(':')[1]}"

        table_json = self.schemas[schema_name]['tables'][table_name]
        column_name = colobj.sqlname
        pkeys = table_json['attributes']['pkeys']

        # PK columns: auto NOT NULL, no separate index; single-column PK
        # also drops the redundant unique (composite PK keeps it, #580).
        if pkeys and (column_name in pkeys.split(',')):
            attributes['notnull'] = '_auto_'
            if ',' not in pkeys:
                attributes.pop('unique', None)
            attributes.pop('indexed', None)

        column_entity = new_column_item(
            schema_name, table_name, column_name, attributes=attributes
        )
        table_json['columns'][colobj.sqlname] = column_entity

    def fill_json_relations_and_indexes(self, colobj):
        colattr = colobj.attributes
        joiner = colobj.relatedColumnJoiner()
        indexed = colattr.get('indexed') or colattr.get('unique')
        dtype_index_config = DTYPE_INDEX_CONFIG.get(colattr.get('dtype'))
        if not indexed and dtype_index_config and dtype_index_config.get('required'):
            indexed = True
        table_name = colobj.table.sqlname
        schema_name = colobj.table.package.sqlname
        table_json = self.schemas[schema_name]['tables'][table_name]
        pkeys = table_json['attributes']['pkeys']
        is_in_pkeys = pkeys and (colobj.name in pkeys.split(','))

        if joiner:
            # FKs always get a supporting index for JOIN performance
            indexed = indexed or True
            relation_info = self._relation_info_from_joiner(colobj, joiner)
            related_to_pkeys = relation_info.pop('related_to_pkeys')
            rel_colobj = relation_info.pop('rel_colobj')
            if joiner.get('foreignkey'):
                self.fill_json_relation(colobj=colobj, attributes=relation_info)
            # FK to a non-PK target needs an index on the target column;
            # deferred because the target table may not be processed yet.
            if not related_to_pkeys:
                self.deferred_indexes.append({"colobj": rel_colobj})

        if indexed and not is_in_pkeys:
            self.fill_json_column_index(colobj=colobj, indexed=indexed)

    def fill_multiple_unique_constraint(self, compositecol):
        colattr = compositecol.attributes
        if not colattr.get('unique'):
            return
        table_name = compositecol.table.sqlname
        schema_name = compositecol.table.package.sqlname
        table_json = self.schemas[schema_name]['tables'][table_name]
        columns = colattr.get('composed_of').split(',')
        constraint_item = new_constraint_item(
            schema_name, table_name, columns, 'UNIQUE'
        )
        table_json['constraints'][constraint_item['entity_name']] = constraint_item

    def statement_converter(self, command):
        """Normalize FK action abbreviations to standard SQL."""
        if not command:
            return None
        command = command.upper()
        if command in ('R', 'RESTRICT'):
            return 'RESTRICT'
        elif command in ('C', 'CASCADE'):
            return 'CASCADE'
        elif command in ('N', 'NO ACTION'):
            return 'NO ACTION'
        elif command in ('SN', 'SETNULL', 'SET NULL'):
            return 'SET NULL'
        elif command in ('SD', 'SETDEFAULT', 'SET DEFAULT'):
            return 'SET DEFAULT'
        return None

    def _relation_info_from_joiner(self, colobj, joiner):
        result = {
            camel_to_snake(k[0:-4]): self.statement_converter(v)
            for k, v in joiner.items() if k.endswith('_sql')
        }
        related_field = joiner['one_relation']
        related_table, related_column = related_field.rsplit('.', 1)
        rel_tblobj = self.model.table(related_table)
        rel_colobj = (
            rel_tblobj.columns.get(related_column)
            or rel_tblobj.composite_columns[related_column]
        )
        result['related_columns'] = (
            rel_colobj.attributes.get('composed_of') or rel_colobj.name
        ).split(',')
        result['related_table'] = rel_tblobj.sqlname
        result['related_schema'] = rel_tblobj.package.sqlname
        result['deferrable'] = joiner.get('deferrable') or joiner.get('deferred')
        result['initially_deferred'] = (
            joiner.get('initially_deferred') or joiner.get('deferred')
        )
        result['related_to_pkeys'] = result['related_columns'] == rel_tblobj.pkeys
        result['rel_colobj'] = rel_colobj
        return result

    def fill_json_relation(self, colobj, attributes=None):
        columns = (
            colobj.attributes.get('composed_of') or colobj.name
        ).split(',')
        table_name = colobj.table.sqlname
        schema_name = colobj.table.package.sqlname
        hashed_entity_name = hashed_name(
            schema=schema_name, table=table_name,
            columns=columns, obj_type='fk'
        )
        attributes['constraint_name'] = hashed_entity_name
        attributes['columns'] = columns
        attributes['constraint_type'] = "FOREIGN KEY"
        relation_item = new_relation_item(
            schema_name, table_name, columns, attributes=attributes
        )
        table_json = self.schemas[schema_name]['tables'][table_name]
        table_json["relations"][relation_item["entity_name"]] = relation_item

    def fill_json_column_index(self, colobj, indexed=None):
        indexed = {} if indexed is True else dict(indexed)
        dtype_index_config = DTYPE_INDEX_CONFIG.get(colobj.attributes.get('dtype'))
        if dtype_index_config:
            indexed.setdefault('method', dtype_index_config.get('method'))
        if colobj.attributes.get('unique'):
            # The DB automatically creates an index for UNIQUE columns
            return
        with_options = {
            k[len('with_'):]: indexed.pop(k)
            for k in list(indexed) if k.startswith('with_')
        }
        sorting = indexed.pop('sorting', None)
        columns = (
            colobj.attributes.get('composed_of') or colobj.name
        ).split(',')
        sorting = sorting.split(',') if sorting else [None] * len(columns)
        table_name = colobj.table.sqlname
        schema_name = colobj.table.package.sqlname
        attributes = dict(
            columns=dict(zip(columns, sorting, strict=False)),
            with_options=with_options,
            **indexed
        )
        index_item = new_index_item(
            schema_name, table_name, columns, attributes=attributes
        )
        table_json = self.schemas[schema_name]['tables'][table_name]
        table_json["indexes"][index_item["entity_name"]] = index_item

    def convert_colattr(self, colattr):
        """Normalize ORM column attributes (dtype/size) for comparison."""
        result = {
            k: v for k, v in colattr.items()
            if k in self.col_json_keys and v is not None
        }
        size = result.pop('size', None)
        dtype = result.pop('dtype', None)
        dtype = GNR_DTYPE_CONVERTER.get(dtype, dtype)

        if size:
            if size.startswith(':'):
                size = f'0{size}'
            if ':' in size:
                dtype = 'A'
            elif ',' not in size:
                # Text types with fixed size -> char
                if not dtype or dtype in ('A', 'T', 'X', 'Z', 'P'):
                    dtype = 'C'
                elif dtype == 'N':
                    size = f'{size},0'

        # char/varchar without size makes no sense -> fallback to text
        if dtype in ('A', 'C') and not size:
            dtype = 'T'

        result['dtype'] = dtype
        if size:
            result['size'] = size
        return result
