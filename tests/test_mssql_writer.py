"""Unit tests for the MSSQL (T-SQL) writer.

Exact-string checks with no database: the full type map, the T-SQL
peculiarities (ADD without COLUMN, one ALTER TABLE per fragment, full
type in ALTER COLUMN), FK RESTRICT -> NO ACTION, filtered index, drop
constraint, and the methods that must raise on unsupported operations.
"""

import pytest

from genro_sqlmigration.exceptions import SqlMigrationError
from genro_sqlmigration.writers import MssqlWriter


class TestTypeMap:
    def setup_method(self):
        self.w = MssqlWriter()

    @pytest.mark.parametrize('dtype,size,expected', [
        ('A', '0:100', 'nvarchar(100)'),
        ('C', '2', 'nchar(2)'),
        ('T', None, 'nvarchar(max)'),
        ('I', None, 'int'),
        ('L', None, 'bigint'),
        ('R', None, 'float'),
        ('N', '14,2', 'numeric(14,2)'),
        ('B', None, 'bit'),
        ('D', None, 'date'),
        ('H', None, 'time'),
        ('DH', None, 'datetime2'),
        ('DHZ', None, 'datetimeoffset'),
        ('O', None, 'varbinary(max)'),
        ('serial', None, 'int IDENTITY(1,1)'),
        ('jsonb', None, 'nvarchar(max)'),
    ])
    def test_column_sql_type(self, dtype, size, expected):
        assert self.w.column_sql_type(dtype, size=size) == expected

    def test_column_definition_notnull_default(self):
        sql = self.w.column_sql_definition(
            'code', 'A', size='0:12', notnull=True, default="'x'"
        )
        assert sql == '"code" nvarchar(12) DEFAULT \'x\' NOT NULL'

    def test_column_definition_serial(self):
        assert self.w.column_sql_definition('id', 'serial') == '"id" int IDENTITY(1,1)'


class TestAlterAssembly:
    def setup_method(self):
        self.w = MssqlWriter()

    def test_add_column_has_no_column_keyword(self):
        assert self.w.add_column_sql('"descr" nvarchar(max)') == 'ADD "descr" nvarchar(max)'

    def test_alter_table_commands_one_per_fragment(self):
        commands = self.w.alter_table_commands(
            'alfa', 'doc', ['ADD "a" int', 'ALTER COLUMN "b" nvarchar(20)']
        )
        assert commands == [
            'ALTER TABLE "alfa"."doc"\nADD "a" int;',
            'ALTER TABLE "alfa"."doc"\nALTER COLUMN "b" nvarchar(20);',
        ]

    def test_alter_column_sql_full_type(self):
        assert self.w.alter_column_sql('name', 'nvarchar(80)') == (
            'ALTER COLUMN "name" nvarchar(80)'
        )


class TestConstraintsAndKeys:
    def setup_method(self):
        self.w = MssqlWriter()

    def test_unique_constraint(self):
        sql = self.w.constraint_sql('cst_1', 'UNIQUE', columns=['a', 'b'])
        assert sql == 'CONSTRAINT "cst_1" UNIQUE ("a", "b")'

    def test_check_constraint(self):
        sql = self.w.constraint_sql('cst_2', 'CHECK', check_clause='rating >= 0')
        assert sql == 'CONSTRAINT "cst_2" CHECK (rating >= 0)'

    def test_drop_constraint(self):
        assert self.w.drop_constraint_sql('cst_3') == 'DROP CONSTRAINT "cst_3"'

    def test_foreign_key_restrict_maps_to_no_action(self):
        sql = self.w.foreign_key_sql(
            'fk_1', ['owner_id'], 'users', 'alfa', ['id'],
            on_delete='RESTRICT', on_update='CASCADE'
        )
        assert sql == (
            'CONSTRAINT "fk_1" FOREIGN KEY ("owner_id") '
            'REFERENCES "alfa"."users" ("id") '
            'ON DELETE NO ACTION ON UPDATE CASCADE'
        )

    def test_foreign_key_ignores_deferrable(self):
        sql = self.w.foreign_key_sql(
            'fk_2', ['a'], 't', 's', ['id'],
            deferrable=True, initially_deferred=True
        )
        assert 'DEFERRABLE' not in sql
        assert sql == (
            'CONSTRAINT "fk_2" FOREIGN KEY ("a") REFERENCES "s"."t" ("id")'
        )


class TestIndexes:
    def setup_method(self):
        self.w = MssqlWriter()

    def test_plain_index(self):
        sql = self.w.create_index_sql(
            'alfa', 'doc', {'title': None}, index_name='idx_1'
        )
        assert sql == 'CREATE INDEX idx_1 ON "alfa"."doc" ("title");'

    def test_filtered_index_with_where(self):
        sql = self.w.create_index_sql(
            'alfa', 'doc', {'title': None}, index_name='idx_2',
            where='title IS NOT NULL'
        )
        assert sql == (
            'CREATE INDEX idx_2 ON "alfa"."doc" ("title") '
            'WHERE title IS NOT NULL;'
        )

    def test_unique_desc_index(self):
        sql = self.w.create_index_sql(
            'alfa', 'doc', {'a': 'DESC', 'b': None}, index_name='idx_3',
            unique=True
        )
        assert sql == (
            'CREATE UNIQUE INDEX idx_3 ON "alfa"."doc" ("a" DESC, "b");'
        )

    def test_method_and_tablespace_ignored(self):
        sql = self.w.create_index_sql(
            'alfa', 'doc', {'title': None}, index_name='idx_4',
            method='gin', tablespace='fast', with_options={'fillfactor': '70'}
        )
        assert sql == 'CREATE INDEX idx_4 ON "alfa"."doc" ("title");'


class TestUnsupportedOperations:
    def setup_method(self):
        self.w = MssqlWriter()

    def test_alter_column_with_conversion_raises(self):
        with pytest.raises(SqlMigrationError):
            self.w.alter_column_with_conversion_sql('c', 'int', 'expr')

    def test_add_not_null_raises(self):
        with pytest.raises(SqlMigrationError):
            self.w.add_not_null_sql('c')

    def test_drop_not_null_raises(self):
        with pytest.raises(SqlMigrationError):
            self.w.drop_not_null_sql('c')

    def test_comment_on_column_raises(self):
        with pytest.raises(SqlMigrationError):
            self.w.comment_on_column_sql('s', 't', 'c', 'x')

    def test_comment_on_table_raises(self):
        with pytest.raises(SqlMigrationError):
            self.w.comment_on_table_sql('s', 't', 'x')

    def test_create_extension_raises(self):
        with pytest.raises(SqlMigrationError):
            self.w.create_extension_sql('unaccent')

    def test_execute_raises(self):
        with pytest.raises(SqlMigrationError):
            self.w.execute('SELECT 1')


class TestDdlCommands:
    def setup_method(self):
        self.w = MssqlWriter()

    def test_create_db_ignores_encoding(self):
        assert self.w.create_db_sql('mydb', encoding='LATIN1') == (
            'CREATE DATABASE "mydb";'
        )

    def test_create_schema(self):
        assert self.w.create_schema_sql('alfa') == 'CREATE SCHEMA "alfa";'

    def test_add_table_pkey(self):
        sql = self.w.add_table_pkey_sql('alfa', 'doc', 'id,code')
        assert sql == (
            'ALTER TABLE "alfa"."doc" ADD CONSTRAINT "doc_pkey" '
            'PRIMARY KEY ("id", "code");'
        )

    def test_drop_table_pkey(self):
        sql = self.w.drop_table_pkey_sql('alfa', 'doc')
        assert sql == 'ALTER TABLE "alfa"."doc" DROP CONSTRAINT "doc_pkey";'
