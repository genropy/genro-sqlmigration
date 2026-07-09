"""Unit tests for the MySQL writer (no database).

Exact-string assertions for the type map, CREATE DATABASE/SCHEMA,
MODIFY COLUMN, foreign keys, DROP CONSTRAINT, CREATE INDEX and the
unsupported operations that must raise.
"""

import pytest

from genro_sqlmigration.exceptions import SqlMigrationError
from genro_sqlmigration.writers import MysqlWriter


class TestTypeMap:
    def setup_method(self):
        self.w = MysqlWriter()

    @pytest.mark.parametrize('dtype,size,expected', [
        ('A', '0:100', 'varchar(100)'),
        ('C', '10', 'char(10)'),
        ('T', None, 'text'),
        ('I', None, 'int'),
        ('L', None, 'bigint'),
        ('R', None, 'double'),
        ('N', '10,2', 'decimal(10,2)'),
        ('B', None, 'boolean'),
        ('D', None, 'date'),
        ('H', None, 'time'),
        ('DH', None, 'datetime'),
        ('DHZ', None, 'timestamp'),
        ('O', None, 'blob'),
        ('serial', None, 'bigint auto_increment'),
        ('jsonb', None, 'json'),
    ])
    def test_column_sql_type(self, dtype, size, expected):
        assert self.w.column_sql_type(dtype, size=size) == expected

    def test_column_definition_notnull_default(self):
        sql = self.w.column_sql_definition(
            'code', 'A', size='0:12', notnull=True, default="'x'"
        )
        assert sql == '"code" varchar(12) DEFAULT \'x\' NOT NULL'


class TestDdlFragments:
    def setup_method(self):
        self.w = MysqlWriter()

    def test_create_db(self):
        assert self.w.create_db_sql('mydb') == (
            'CREATE DATABASE "mydb" CHARACTER SET utf8mb4;'
        )

    def test_create_db_ignores_encoding(self):
        assert self.w.create_db_sql('mydb', encoding='LATIN1') == (
            'CREATE DATABASE "mydb" CHARACTER SET utf8mb4;'
        )

    def test_create_schema(self):
        assert self.w.create_schema_sql('alfa') == 'CREATE SCHEMA "alfa";'

    def test_table_fullname(self):
        assert self.w.table_fullname('alfa', 'doc') == '"alfa"."doc"'

    def test_modify_column(self):
        assert self.w.alter_column_sql('descr', 'varchar(200)') == (
            'MODIFY COLUMN "descr" varchar(200)'
        )

    def test_drop_constraint(self):
        assert self.w.drop_constraint_sql('cst_abc') == 'DROP CONSTRAINT "cst_abc"'

    def test_foreign_key_plain(self):
        sql = self.w.foreign_key_sql(
            'fk_1', ['owner_id'], 'users', 'alfa', ['id']
        )
        assert sql == (
            'CONSTRAINT "fk_1" FOREIGN KEY ("owner_id") '
            'REFERENCES "alfa"."users" ("id")'
        )

    def test_foreign_key_on_delete_update(self):
        sql = self.w.foreign_key_sql(
            'fk_1', ['owner_id'], 'users', 'alfa', ['id'],
            on_delete='CASCADE', on_update='RESTRICT',
            deferrable=False, initially_deferred=False,
        )
        assert sql == (
            'CONSTRAINT "fk_1" FOREIGN KEY ("owner_id") '
            'REFERENCES "alfa"."users" ("id") '
            'ON DELETE CASCADE ON UPDATE RESTRICT'
        )

    def test_unique_constraint(self):
        sql = self.w.constraint_sql('cst_1', 'UNIQUE', columns=['a', 'b'])
        assert sql == 'CONSTRAINT "cst_1" UNIQUE ("a", "b")'

    def test_check_constraint(self):
        sql = self.w.constraint_sql('cst_1', 'CHECK', check_clause='"rating" >= 0')
        assert sql == 'CONSTRAINT "cst_1" CHECK ("rating" >= 0)'


class TestCreateIndex:
    def setup_method(self):
        self.w = MysqlWriter()

    def test_plain_index(self):
        sql = self.w.create_index_sql(
            'alfa', 'doc', {'title': None}, index_name='idx_1'
        )
        assert sql == 'CREATE INDEX idx_1 ON "alfa"."doc" ("title");'

    def test_unique_index(self):
        sql = self.w.create_index_sql(
            'alfa', 'doc', {'title': None}, index_name='idx_1', unique=True
        )
        assert sql == 'CREATE UNIQUE INDEX idx_1 ON "alfa"."doc" ("title");'

    def test_desc_index(self):
        sql = self.w.create_index_sql(
            'alfa', 'doc', {'title': 'DESC', 'code': None}, index_name='idx_1'
        )
        assert sql == 'CREATE INDEX idx_1 ON "alfa"."doc" ("title" DESC, "code");'


class TestUnsupported:
    def setup_method(self):
        self.w = MysqlWriter()

    def test_conversion_raises(self):
        with pytest.raises(SqlMigrationError):
            self.w.alter_column_with_conversion_sql('c', 'int', 'expr')

    def test_add_not_null_raises(self):
        with pytest.raises(SqlMigrationError):
            self.w.add_not_null_sql('c')

    def test_drop_not_null_raises(self):
        with pytest.raises(SqlMigrationError):
            self.w.drop_not_null_sql('c')

    def test_comment_column_raises(self):
        with pytest.raises(SqlMigrationError):
            self.w.comment_on_column_sql('alfa', 'doc', 'c', 'x')

    def test_comment_table_raises(self):
        with pytest.raises(SqlMigrationError):
            self.w.comment_on_table_sql('alfa', 'doc', 'x')

    def test_create_extension_raises(self):
        with pytest.raises(SqlMigrationError):
            self.w.create_extension_sql('uuid-ossp')

    def test_execute_raises(self):
        with pytest.raises(SqlMigrationError):
            self.w.execute('SELECT 1')
