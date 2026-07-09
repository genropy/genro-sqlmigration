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


class SqlConnectionException(SqlMigrationError):
    """Raised when the database server is unreachable or the connection
    fails for reasons other than a non-existing database (e.g. wrong
    host, port, network timeout)."""

    def __init__(self, dbname, original_error=None):
        self.dbname = dbname
        self.original_error = original_error

    def __str__(self):
        return f"Cannot connect to database '{self.dbname}': {self.original_error}"
