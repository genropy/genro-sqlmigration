"""Unit tests for :class:`SqliteWriter` (no database).

Exact-SQL oracles for the declared-type map, column/add-column fragments,
one-per-fragment ALTER TABLE assembly, CREATE INDEX variants and the
qualified table name; plus the explicit errors of the unsupported DDL
methods.
"""

import pytest

from genro_sqlmigration.exceptions import SqlMigrationError
from genro_sqlmigration.writers import SqliteWriter


@pytest.fixture
def writer():
    return SqliteWriter()


class TestColumnSqlType:
    @pytest.mark.parametrize('dtype, size, expected', [
        ('A', '0:100', 'varchar(100)'),
        ('C', '5', 'char(5)'),
        ('T', None, 'text'),
        ('I', None, 'integer'),
        ('L', None, 'bigint'),
        ('R', None, 'real'),
        ('N', '10,2', 'numeric(10,2)'),
        ('B', None, 'boolean'),
        ('D', None, 'date'),
        ('H', None, 'time'),
        ('DH', None, 'timestamp without time zone'),
        ('DHZ', None, 'timestamp with time zone'),
        ('O', None, 'blob'),
        ('serial', None, 'integer'),
        ('jsonb', None, 'jsonb'),
    ])
    def test_type_map(self, writer, dtype, size, expected):
        assert writer.column_sql_type(dtype, size=size) == expected


class TestColumnDefinition:
    def test_plain(self, writer):
        assert writer.column_sql_definition('title', 'A', size='0:100') == (
            '"title" varchar(100)'
        )

    def test_notnull_and_default(self, writer):
        assert writer.column_sql_definition(
            'n', 'I', notnull=True, default='0'
        ) == '"n" integer DEFAULT 0 NOT NULL'


class TestAddColumn:
    def test_add_column_fragment(self, writer):
        assert writer.add_column_sql('"extra" text') == 'ADD COLUMN "extra" text'


class TestAlterTableCommands:
    def test_one_statement_per_fragment(self, writer):
        commands = writer.alter_table_commands(
            'alfa', 'doc', ['ADD COLUMN "a" text', 'ADD COLUMN "b" integer']
        )
        assert commands == [
            'ALTER TABLE "alfa"."doc"\nADD COLUMN "a" text;',
            'ALTER TABLE "alfa"."doc"\nADD COLUMN "b" integer;',
        ]


class TestCreateIndex:
    def test_plain(self, writer):
        assert writer.create_index_sql(
            'alfa', 'doc', {'title': None}, index_name='idx_x'
        ) == 'CREATE INDEX "alfa"."idx_x" ON "doc" ("title");'

    def test_unique(self, writer):
        assert writer.create_index_sql(
            'alfa', 'doc', {'code': None}, index_name='idx_u', unique=True
        ) == 'CREATE UNIQUE INDEX "alfa"."idx_u" ON "doc" ("code");'

    def test_desc(self, writer):
        assert writer.create_index_sql(
            'alfa', 'doc', {'a': 'DESC', 'b': None}, index_name='idx_d'
        ) == 'CREATE INDEX "alfa"."idx_d" ON "doc" ("a" DESC, "b");'

    def test_where(self, writer):
        assert writer.create_index_sql(
            'alfa', 'doc', {'ref': None}, index_name='idx_w',
            where='ref IS NOT NULL'
        ) == (
            'CREATE INDEX "alfa"."idx_w" ON "doc" ("ref") '
            'WHERE ref IS NOT NULL;'
        )

    def test_list_columns(self, writer):
        assert writer.create_index_sql(
            'alfa', 'doc', ['a', 'b'], index_name='idx_l'
        ) == 'CREATE INDEX "alfa"."idx_l" ON "doc" ("a", "b");'


class TestTableFullname:
    def test_quoted(self, writer):
        assert writer.table_fullname('alfa', 'doc') == '"alfa"."doc"'


class TestConstraintSql:
    def test_unique(self, writer):
        assert writer.constraint_sql('cst_x', 'UNIQUE', columns=['a', 'b']) == (
            'CONSTRAINT "cst_x" UNIQUE ("a", "b")'
        )

    def test_check(self, writer):
        assert writer.constraint_sql(
            'cst_c', 'CHECK', check_clause='rating >= 0'
        ) == 'CONSTRAINT "cst_c" CHECK (rating >= 0)'


class TestUnsupportedRaise:
    @pytest.mark.parametrize('call', [
        lambda w: w.create_db_sql('db'),
        lambda w: w.create_schema_sql('alfa'),
        lambda w: w.alter_column_sql('c', 'text'),
        lambda w: w.alter_column_with_conversion_sql('c', 'text', 'expr'),
        lambda w: w.add_not_null_sql('c'),
        lambda w: w.drop_not_null_sql('c'),
        lambda w: w.drop_constraint_sql('cst'),
        lambda w: w.drop_table_pkey_sql('alfa', 'doc'),
        lambda w: w.add_table_pkey_sql('alfa', 'doc', 'id'),
        lambda w: w.create_extension_sql('unaccent'),
        lambda w: w.comment_on_column_sql('alfa', 'doc', 'c', 'x'),
        lambda w: w.comment_on_table_sql('alfa', 'doc', 'x'),
        lambda w: w.foreign_key_sql('fk', ['a'], 'other', 'alfa', ['id']),
        lambda w: w.execute('SELECT 1'),
    ])
    def test_raises_sql_migration_error(self, writer, call):
        with pytest.raises(SqlMigrationError, match='not supported by SQLite'):
            call(writer)
