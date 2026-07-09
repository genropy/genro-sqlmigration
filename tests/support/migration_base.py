"""Base class for the migration oracle tests.

Replicates the legacy ``BaseGnrSqlMigration`` flow with the one M1
adaptation: fixtures build the ormStructure JSON through
``OrmJsonProducer`` and inject it into the migrator, instead of letting
an ORM extractor read a live model. The SQL oracles stay byte-identical.

``checkChanges`` is the heart of the suite: exact (whitespace-normalized)
SQL comparison, then apply, then re-diff asserting idempotence.
"""

from genro_sqlmigration import SqlMigrator

from .orm_producer import OrmJsonProducer, SrcModel
from .pg_database import PgTestDatabase
from .sqltools import normalize_sql


class BaseMigrationTest:
    """Per-class environment: model, database facade, producer, migrator.

    Subclasses set ``dbname`` (one database per class) and optionally
    ``migrator_kwargs``. Test methods run in definition order and share
    the class-level model/database state, as in the legacy suite.
    """

    dbname = None
    migrator_kwargs = {'removeDisabled': False}

    @classmethod
    def setup_env(cls, pg_conf):
        cls.pg_conf = pg_conf
        cls.model = SrcModel()
        cls.src = cls.model
        cls.db = PgTestDatabase(cls.dbname, pg_conf, cls.model)
        cls.producer = OrmJsonProducer(cls.model, cls.dbname)
        cls.migrator = SqlMigrator(cls.db, **cls.migrator_kwargs)
        cls.db.dropDb(cls.dbname)

    @classmethod
    def teardown_env(cls):
        cls.db.closeConnection()
        cls.db.dropDb(cls.dbname)

    def new_migrator(self, **overrides):
        """Replace the class migrator (legacy tests re-instantiate it)."""
        kwargs = dict(type(self).migrator_kwargs)
        kwargs.update(overrides)
        type(self).migrator = SqlMigrator(self.db, **kwargs)
        return type(self).migrator

    def startup(self):
        """Rebuild the ormStructure from the model and inject it."""
        self.migrator.ormStructure = self.producer.get_json_struct(
            extensions=self.migrator.extensions
        )

    def checkChanges(self, expected_value=None, apply_only=False,
                     skip_recheck=False):
        """Validate expected SQL, apply it, then assert idempotence.

        With ``apply_only`` the changes are applied and returned without
        comparison. ``skip_recheck`` skips the idempotence re-diff (used
        by backup-mode tests: backup columns exist only on the DB side).
        """
        self.startup()
        self.migrator.prepareMigrationCommands()
        if apply_only:
            changes = self.migrator.getChanges()
            self.migrator.applyChanges()
            return changes
        if expected_value == '?':
            print('Expected value:', self.migrator.getChanges())
            return
        normalized_expected_value = normalize_sql(expected_value)
        changes = self.migrator.getChanges()
        normalized_changes = normalize_sql(changes)
        if normalized_changes != normalized_expected_value:
            print('Actual changes:', changes)
            print('ORM Structure:', self.migrator.ormStructure)
            print('SQL Structure:', self.migrator.sqlStructure)
            assert normalized_changes == normalized_expected_value, (
                'Mismatch in expected SQL commands.'
            )
        else:
            self.migrator.applyChanges()
            if not skip_recheck:
                # Rebuild the ormStructure before re-diffing, as the legacy
                # OrmExtractor did on every prepare: command handlers mark
                # transient state (e.g. ``_rebuilt``) on the injected JSON.
                self.startup()
                self.migrator.prepareMigrationCommands()
                changes = self.migrator.getChanges()
                if changes:
                    print('unexpected changes', changes)
                assert not changes, 'Failed to execute SQL command as expected.'
