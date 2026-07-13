# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0

"""
app.py - the DB <-> XML editor as a web/MCP application
=======================================================

Exposes the transport-agnostic editor operations from
:mod:`editor_service` as an HTTP + MCP application, built on genro-asgi's
``McpOpenApiApplication`` ("one router, two faces": the same routes are
reachable as REST endpoints and as MCP tools).

genro-asgi is an optional dependency: install the ``app`` extra
(``pip install genro-sqlmigration[app,postgresql]``). The import lives in
this module only, so the core package stays installable with just
``dictdiffer`` and its driver. genro-asgi is a sibling ``genro-*`` package,
not the legacy ``gnr.*`` — importing it does not break the autonomy rule.

Launch with the genro-asgi CLI (no server code of our own)::

    genro-asgi serve application=src/genro_sqlmigration/app.py:EditorApp

Endpoints then live at ``/introspect``, ``/migrate``, ``/apply``; the MCP
JSON-RPC face is at ``/mcp`` and Swagger UI at ``/_meta/docs``.

Safety: ``introspect`` and ``migrate`` (dry-run: it returns the SQL diff,
never executes) are exposed as MCP tools; ``apply`` — which runs DDL on a
live database — is deliberately REST-only, so an agent cannot apply a
destructive migration through the MCP face.
"""

from genro_asgi import route
from genro_asgi.applications.openapi_application import McpOpenApiApplication

# Absolute import: the genro-asgi CLI can load this module as a standalone
# file (application=.../app.py:EditorApp), where a relative import has no
# parent package. The package is installed, so the absolute form works both
# as a file target and as a module target.
from genro_sqlmigration.editor_service import introspect_to_xml, migrate_from_xml


def _connection_params(host, port, user, password, dbname):
    """Assemble the psycopg connection dict, dropping unset values."""
    params = {"host": host, "port": port, "user": user,
              "password": password, "dbname": dbname}
    return {k: v for k, v in params.items() if v is not None}


def _schema_list(schemas):
    """Turn a comma-joined schema string into a list (None if empty)."""
    return [s.strip() for s in schemas.split(",") if s.strip()] if schemas else None


class EditorApp(McpOpenApiApplication):
    """DB <-> XML editor, exposed to browsers and to agents."""

    openapi_info = {
        "title": "genro-sqlmigration editor",
        "version": "1.0.0",
        "description": "Introspect a database to SQL-model XML and migrate "
                       "a database to match an edited XML.",
    }

    @route(media_type="application/xml", channel_channels="mcp,rest")
    def introspect(self, host=None, port=None, user=None, password=None,
                   dbname=None, schemas=None):
        """Read a live database's structure and return it as SQL-model XML.

        Connection is given field by field (host, port, user, password,
        dbname). ``schemas`` optionally limits introspection to a
        comma-separated list of schema names.
        """
        return introspect_to_xml(
            _connection_params(host, port, user, password, dbname),
            schemas=_schema_list(schemas),
        )

    @route(media_type="application/json", channel_channels="mcp,rest")
    def migrate(self, xml="", host=None, port=None, user=None, password=None,
                dbname=None, schemas=None):
        """Dry-run: return the SQL that would align the database to the XML.

        Diffs the edited SQL-model ``xml`` against the live database and
        returns the migration SQL without executing it (empty string means
        the database already matches). Use ``apply`` to execute it.
        """
        sql = migrate_from_xml(
            _connection_params(host, port, user, password, dbname),
            xml, apply=False, schemas=_schema_list(schemas),
        )
        return {"sql": sql}

    @route(media_type="application/json")
    def apply(self, xml="", host=None, port=None, user=None, password=None,
              dbname=None, schemas=None):
        """REST-only: execute the migration that aligns the database to the XML.

        Not an MCP tool by design — it runs DDL on a live database, so it
        stays a deliberate REST call rather than an agent-callable tool.
        """
        sql = migrate_from_xml(
            _connection_params(host, port, user, password, dbname),
            xml, apply=True, schemas=_schema_list(schemas),
        )
        return {"sql": sql, "applied": True}
