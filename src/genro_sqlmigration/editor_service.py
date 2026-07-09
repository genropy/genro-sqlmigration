# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""
editor_service.py - the DB <-> XML round-trip logic (transport-agnostic)
========================================================================

The two operations the editor exposes, as plain functions with no web
framework: introspect a live database into the natural SQL-model XML, and
migrate a database to match an edited XML. Keeping the logic here (not in the
server) means the round-trip is testable directly against a database; the
genro-asgi server is a thin wrapper over these.

Both take ``connection_params`` — the dict the tool asks for
(``{host, port, user, password, dbname}``) — so the tool is agnostic about
which database it targets.
"""

from .adapters import PgDatabase
from .migrator import SqlMigrator
from .validation import StructureValidator
from .xml_producer import XmlStructureProducer, struct_to_xml


def introspect_to_xml(connection_params, schemas=None):
    """Read a live database's structure and return it as natural SQL-model XML.

    ``schemas`` limits the introspection to the given schema names (default:
    the adapter's application schemas). The XML is the editable, GUI-friendly
    form — the entry point of the DB → XML → edit → migrate round-trip.
    """
    db = PgDatabase(connection_params, application_schemas=schemas)
    structure = db.adapter.reader.get_json_struct(db.get_dbname(), schemas=schemas)
    return struct_to_xml(structure)


def migrate_from_xml(connection_params, xml, apply=False, schemas=None):
    """Migrate a database to match an edited SQL-model XML.

    Projects the XML to the normalized JSON (validated), diffs it against the
    live structure, and returns the SQL commands. With ``apply=True`` the
    commands are also executed. Returns the SQL diff (empty string when the
    database already matches — the idempotence signal).
    """
    db = PgDatabase(connection_params, application_schemas=schemas)
    migrator = SqlMigrator(db, removeDisabled=False)
    migrator.ormStructure = StructureValidator().validate(
        XmlStructureProducer(xml).get_json_struct()
    )
    migrator.prepareMigrationCommands()
    changes = migrator.getChanges()
    if apply:
        migrator.applyChanges()
    return changes
