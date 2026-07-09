"""
exceptions.py - Migration system exceptions
=============================================

Local exception classes replacing Genropy-specific exceptions.
These provide the same interface used by command_builder,
db_extractor and migrator.
"""


class SqlMigrationError(Exception):
    """Base exception for the SQL migration system."""


class NonExistingDbException(SqlMigrationError):
    """Raised when the target database does not exist yet."""


class SqlValidationError(SqlMigrationError):
    """Raised when a normalized JSON structure violates the contract.

    Carries the full list of problems found, so producers can fix
    them all in one pass.
    """

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__('\n'.join(self.errors))


class SqlConnectionException(SqlMigrationError):
    """Raised when the database server is unreachable or the connection
    fails for reasons other than a non-existing database (e.g. wrong
    host, port, network timeout)."""

    def __init__(self, dbname, original_error=None):
        self.dbname = dbname
        self.original_error = original_error

    def __str__(self):
        return f"Cannot connect to database '{self.dbname}': {self.original_error}"
