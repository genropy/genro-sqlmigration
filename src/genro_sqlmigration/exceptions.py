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
