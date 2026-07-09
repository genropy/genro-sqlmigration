"""
migrator.py - SQL migration orchestrator
==========================================

This module contains the :class:`SqlMigrator` class, the main entry point
of the migration system. SqlMigrator **composes** the three mixins
that implement the different phases of migration:

- :class:`DiffMixin` (from ``diff_engine.py``): ORM vs DB comparison
- :class:`CommandBuilderMixin` (from ``command_builder.py``): SQL generation
- :class:`ExecutorMixin` (from ``executor.py``): assembly and execution

Architecture
-------------

SqlMigrator follows the **orchestrator** pattern: it does not directly
implement comparison or SQL generation logic, but coordinates the
components that do. Its responsibilities are:

1. **Initialization**: creates the two extractors (ORM and DB) and
   configures migration parameters (force, backup, etc.)

2. **Structure preparation**: invokes the extractors to produce the
   two JSON representations (ORM and DB)

3. **Diff computation**: delegates to DiffMixin to produce events

4. **Command generation**: delegates to CommandBuilderMixin to create SQL

5. **Execution**: delegates to ExecutorMixin to apply changes

Complete migration flow
------------------------

::

    SqlMigrator
        |
        +-- __init__()
        |   +-- OrmExtractor(migrator=self, extensions=...)
        |
        +-- prepareStructures()
        |   +-- extractOrm() -> ormExtractor.get_json_struct()
        |   +-- extractSql() -> db.adapter.reader.get_json_struct(dbname, schemas)
        |
        +-- prepareMigrationCommands()  [uses DiffMixin + CommandBuilderMixin]
        |   +-- dictDifferChanges()  -> added/changed/removed events
        |   +-- handler(**kw)  -> SQL commands in self.commands
        |
        +-- getChanges()  [uses ExecutorMixin]
        |   +-- sqlCommandsForTable()  -> assembled SQL
        |
        +-- applyChanges()  [uses ExecutorMixin]
            +-- db.adapter.execute()

Configuration parameters
--------------------------

- **extensions**: list of PostgreSQL extensions to include in migration
  (e.g. "uuid-ossp,pg_trgm"). Also auto-detected from ORM columns.

- **ignore_constraint_name** (default True): ignores differences in
  constraint and index names. Useful because the ORM generates hashed
  names that may differ from manually created names in the DB.

- **excludeReadOnly** (default True): excludes from migration packages
  marked as readOnly (typically system or third-party packages).

- **removeDisabled** (default True): disables generation of removal
  commands (DROP TABLE, DROP COLUMN, etc.). For safety, migration
  never removes entities from the DB unless explicitly enabled.

- **force** (default False): forces type conversions even on non-empty
  columns. Incompatible values become NULL.

- **backup** (default False, implies force): before a type conversion,
  creates a backup column with the original data. Allows verifying
  the conversion and recovering lost data.

Usage
------

Typical usage example::

    from genro_sqlmigration import SqlMigrator

    # Compute differences without applying them
    migrator = SqlMigrator(app.db)
    sql = migrator.getChanges()
    print(sql)

    # Apply differences
    migrator.applyChanges()

    # With backup for type conversions
    migrator = SqlMigrator(app.db, force=True, backup=True)
    migrator.applyChanges()
    report = migrator.verifyConversionBackups()
"""

from .command_builder import CommandBuilderMixin
from .diff_engine import DiffMixin
from .exceptions import NonExistingDbException
from .executor import ExecutorMixin
from .structures import nested_defaultdict


class SqlMigrator(DiffMixin, CommandBuilderMixin, ExecutorMixin):
    """Main SQL migration orchestrator.

    Coordinates ORM and DB structure extraction, comparison,
    SQL command generation and execution.

    Inherits from mixins:
    - :class:`DiffMixin`: ``diff`` property, ``dictDifferChanges()``,
      ``getDiffBag()``
    - :class:`CommandBuilderMixin`: ``added_*``, ``changed_*``,
      ``removed_*`` handlers, SQL helpers
    - :class:`ExecutorMixin`: ``getChanges()``, ``applyChanges()``,
      ``verifyConversionBackups()``

    Args:
        db: GnrSqlDb database object.
        extensions: Comma-separated string of extensions (optional).
        ignore_constraint_name: If True, ignores differences in constraint
            names (default True).
        excludeReadOnly: If True, excludes readOnly packages (default True).
        removeDisabled: If True, does not generate removal commands (default True).
        force: If True, forces type conversions on non-empty columns
            (default False).
        backup: If True, creates backups before conversions. Implies force
            (default False).
    """

    def __init__(self, db,
                 extensions=None,
                 ignore_constraint_name=True,
                 excludeReadOnly=True,
                 removeDisabled=True,
                 force=False,
                 backup=False):
        self.db = db
        self.extensions = extensions.split(',') if extensions else []
        self.commands = {}
        self.sql_commands = {
            'db_creation': None,
            'build_commands': None,
            'extensions_commands': None
        }
        self.excludeReadOnly = excludeReadOnly
        self.removeDisabled = removeDisabled
        self.ignore_constraint_name = ignore_constraint_name
        self.force = force or backup  # backup implies force
        self.backup = backup
        self.ormStructure = {}

    def prepareMigrationCommands(self):
        """Prepare migration commands by comparing ORM and DB.

        This is the method that orchestrates the complete flow:

        1. Calls ``prepareStructures()`` to extract ORM and DB
        2. Initializes ``self.commands`` as a nested defaultdict
        3. Iterates over diff events (from DiffMixin)
        4. For each event, calls the corresponding handler
           (from CommandBuilderMixin) which populates ``self.commands``

        'removed' events are skipped if ``removeDisabled`` is True.
        Entities in readOnly schemas are ignored.
        """
        self.prepareStructures()
        self.commands = nested_defaultdict()
        for evt, kw in self.dictDifferChanges():
            if evt == 'removed' and self.removeDisabled:
                continue
            item = kw['item']
            if item.get('schema_name') in self.readOnly_schemas:
                continue
            handler = getattr(
                self, f'{evt}_{kw["entity"]}', self.missing_handler_cb
            )
            handler(**kw)

    def prepareStructures(self):
        """Prepare JSON structures from the ORM and the database.

        Determines which schemas to inspect:
        - ``application_schemas``: schemas of the application's packages
        - ``readOnly_schemas``: schemas to exclude if excludeReadOnly is True
        - ``tenant_schemas``: multi-tenant schemas

        Then invokes the two extractors in sequence.
        """
        self.application_schemas = self.db.getApplicationSchemas()
        self.readOnly_schemas = self.db.readOnlySchemas()
        if self.excludeReadOnly:
            self.application_schemas = [
                schema for schema in self.application_schemas
                if schema not in self.readOnly_schemas
            ]
        try:
            self.tenant_schemas = self.db.getTenantSchemas()
        except NonExistingDbException:
            self.tenant_schemas = []
        self.extractOrm()
        self.extractSql(
            schemas=self.application_schemas + self.tenant_schemas
        )

    def extractOrm(self):
        """Keep the caller-injected ORM structure.

        The package has no ORM extractor by design: the producer builds
        the normalized JSON and assigns it to ``ormStructure`` before
        running the migration. This hook guarantees the attribute is a
        dict and strips the optional ``format_version`` envelope key
        (validated at the boundary, never diffed).
        """
        self.ormStructure = self.ormStructure or {}
        if 'format_version' in self.ormStructure:
            self.ormStructure = {
                k: v for k, v in self.ormStructure.items() if k == 'root'
            }

    def extractSql(self, schemas=None):
        """Extract the JSON structure from the actual database.

        Args:
            schemas: List of schemas to inspect.
        """
        self.sqlStructure = self.db.adapter.reader.get_json_struct(self.db.get_dbname(), schemas=schemas)

    def clearSql(self):
        """Reset the SQL structure extracted from the database."""
        self.sqlStructure = {}

    def clearOrm(self):
        """Reset the ORM structure."""
        self.ormStructure = {}

    def clearCommands(self):
        """Reset the DB creation commands to force regeneration."""
        self.commands.pop('db', None)
