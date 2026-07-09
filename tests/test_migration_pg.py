"""The base oracle suite: exact-SQL migration tests on live PostgreSQL.

Ported from the legacy ``BaseGnrSqlMigration`` (79-method suite,
``gnrpy/tests/sql/test_gnrsqlmigration.py``). Fixtures build the JSON
through ``tests/support/orm_producer.py`` instead of the Genropy ORM;
the expected SQL strings are byte-identical to the legacy oracles
(including the hashed ``cst_*``/``fk_*``/``idx_*`` names).

Covers: database/schema/table creation, columns, primary keys (single
and composite), UNIQUE (single and multi-column), foreign keys (single,
multi, to non-PK targets with auto index, ON DELETE/deferred), indexes
(btree/gin, #626 and #629), and the full dtype conversion matrix
(12a-12q, USING expressions).

Tests run in definition order and accumulate state, as in the legacy
suite. The idempotence assertion inside ``checkChanges`` re-runs the
migrator after every apply and requires zero residual changes.
"""

import pytest

from .support.migration_base import BaseMigrationTest


@pytest.mark.postgresql
@pytest.mark.usefixtures('migration_env')
class TestSqlMigration(BaseMigrationTest):
    """Creation of db, schemas, tables, columns, constraints and indexes."""

    dbname = 'test_gnrsqlmigration'

    def test_01_create_db(self):
        """Tests database creation with the specified encoding."""
        check_value = """CREATE DATABASE "test_gnrsqlmigration" ENCODING 'UNICODE';\n"""
        self.checkChanges(check_value)

    def test_02_create_schema(self):
        """Tests schema creation in the database."""
        self.src.package('alfa', sqlschema='alfa')
        check_value = 'CREATE SCHEMA "alfa";'
        self.checkChanges(check_value)

    def test_03_create_table_nopkey(self):
        """Tests creation of a table without a primary key."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('recipe')
        tbl.column('code', size=':12')
        check_value = 'CREATE TABLE "alfa"."alfa_recipe" ("code" character varying(12));'
        self.checkChanges(check_value)

    def test_04_add_column(self):
        """Tests adding a new column to an existing table."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('recipe')
        tbl.column('description')
        check_value = 'ALTER TABLE "alfa"."alfa_recipe" \n ADD COLUMN "description" text;'
        self.checkChanges(check_value)

    def test_04d_add_numeric_column(self):
        """Tests adding a numeric column to an existing table."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('recipe')
        tbl.column('cost', dtype='N', size='14,2')
        check_value = 'ALTER TABLE "alfa"."alfa_recipe" \n ADD COLUMN "cost" numeric(14,2) ;'
        self.checkChanges(check_value)

    def test_04b_add_indexed_columns(self):
        """Tests adding indexed columns to an existing table."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('recipe')
        tbl.column('ins_ts', dtype='DH', indexed=True)
        tbl.column('recipy_type', size=':2', indexed=True)

        check_value = 'ALTER TABLE "alfa"."alfa_recipe" \n ADD COLUMN "ins_ts" timestamp without time zone ,\nADD COLUMN "recipy_type" character varying(2) ;\nCREATE INDEX idx_f473bae1 ON "alfa"."alfa_recipe" USING btree ("ins_ts") ;\nCREATE INDEX idx_490f54d9 ON "alfa"."alfa_recipe" USING btree ("recipy_type") ;'
        self.checkChanges(check_value)

    def test_04c_add_unique_multiple_constraint(self):
        pkg = self.src.package('alfa')
        tbl = pkg.table('restaurant', pkey='id')
        tbl.column('id', dtype='serial')
        tbl.column('name', size=':45')
        tbl.column('country', size='2')
        tbl.column('vat_number', size=':30')
        tbl.compositeColumn('international_vat', columns='country,vat_number', unique=True)
        check_value = 'CREATE TABLE "alfa"."alfa_restaurant"(\n "id" serial8 NOT NULL,\n "name" character varying(45),\n "country" character(2),\n "vat_number" character varying(30),\n PRIMARY KEY (id),\n CONSTRAINT "cst_703bf76b" UNIQUE ("country", "vat_number")\n);'
        self.checkChanges(check_value)

    def test_04d_add_unique_column(self):
        """Tests adding a unique column to an existing table."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('recipe')
        tbl.column('testuniquecol', unique=True, size=':10')
        check_value = 'ALTER TABLE "alfa"."alfa_recipe"\nADD COLUMN "testuniquecol" character varying(10);\nALTER TABLE "alfa"."alfa_recipe"\nADD CONSTRAINT "cst_f797d32c" UNIQUE ("testuniquecol");'
        self.checkChanges(check_value)

    def test_04e_add_column_with_gin_index(self):
        """indexed=dict(method='gin') generates USING gin (issue #626)."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('recipe')
        tbl.column('search_tsv', dtype='TSV', indexed={'method': 'gin'})
        check_value = 'ALTER TABLE "alfa"."alfa_recipe" \n ADD COLUMN "search_tsv" tsvector ;\nCREATE INDEX idx_1a420e6d ON "alfa"."alfa_recipe" USING gin ("search_tsv") ;'
        self.checkChanges(check_value)

    def test_04f_tsv_indexed_true_auto_gin(self):
        """dtype='TSV' with indexed=True auto-generates a GIN index (#626/#629).

        A btree index on tsvector is never useful and fails on large
        documents; TSV columns must default to GIN.
        """
        pkg = self.src.package('alfa')
        tbl = pkg.table('recipe')
        tbl.column('content_tsv', dtype='TSV', indexed=True)
        check_value = 'ALTER TABLE "alfa"."alfa_recipe" \n ADD COLUMN "content_tsv" tsvector ;\nCREATE INDEX idx_0ae87617 ON "alfa"."alfa_recipe" USING gin ("content_tsv") ;'
        self.checkChanges(check_value)

    def test_05a_create_table_withpkey(self):
        """Tests creating a table with a primary key column."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('ingredient', pkey='id')
        tbl.column('id', dtype='serial')
        tbl.column('description')
        check_value = 'CREATE TABLE "alfa"."alfa_ingredient" ("id" serial8 NOT NULL, "description" text, PRIMARY KEY (id));'
        self.checkChanges(check_value)

    def test_05b_add_primary_key(self):
        """Tests adding a primary key to an existing table."""
        pkg = self.src.package('alfa')
        pkg.table('recipe', pkey='code')  # Primary key defined as a table attribute
        check_value = 'ALTER TABLE "alfa"."alfa_recipe" DROP CONSTRAINT IF EXISTS alfa_recipe_pkey;\nALTER TABLE "alfa"."alfa_recipe" ADD PRIMARY KEY (code);'
        self.checkChanges(check_value)

    def test_05c_create_table_withCompositePkey(self):
        pkg = self.src.package('alfa')
        tbl = pkg.table('recipe_row', pkey='composite_key')
        tbl.column('recipe_code', size=':12')
        tbl.column('recipe_line', dtype='L')
        tbl.compositeColumn('composite_key', columns='recipe_code,recipe_line')
        tbl.column('description')
        tbl.column('ingredient_id', dtype='L')
        check_value = 'CREATE TABLE "alfa"."alfa_recipe_row" ("recipe_code" character varying(12) NOT NULL , "recipe_line" bigint NOT NULL , "description" text , "ingredient_id" bigint , PRIMARY KEY (recipe_code,recipe_line));'
        self.checkChanges(check_value)

    def test_05c1_composite_pkey_with_unique_column(self):
        """Columns in a composite PK retain individual uniques (#576/#580)."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('test_composite_unique', pkey='composite_key')
        tbl.compositeColumn('composite_key', columns='field_a,field_b')
        tbl.column('field_a', size='5')
        tbl.column('field_b', size='5', unique=True)
        tbl.column('field_c', size='5', unique=True)
        check_value = ('CREATE TABLE "alfa"."alfa_test_composite_unique"(\n'
                       ' "field_a" character(5) NOT NULL,\n'
                       ' "field_b" character(5) NOT NULL,\n'
                       ' "field_c" character(5),\n'
                       ' PRIMARY KEY (field_a,field_b)\n'
                       ');\n'
                       'ALTER TABLE "alfa"."alfa_test_composite_unique"\n'
                       'ADD CONSTRAINT "cst_f65e94e4" UNIQUE ("field_b");\n'
                       'ALTER TABLE "alfa"."alfa_test_composite_unique"\n'
                       'ADD CONSTRAINT "cst_7db93e7e" UNIQUE ("field_c");')
        self.checkChanges(check_value)

    def test_05d_create_table_with_pkey_explicit_unique(self):
        """Single-column PK drops the redundant explicit unique."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('company', pkey='code')
        tbl.column('code', size=':30', unique=True)
        tbl.column('description')
        check_value = 'CREATE TABLE "alfa"."alfa_company"(\n "code" character varying(30) NOT NULL,\n "description" text,\n PRIMARY KEY (code)\n);'
        self.checkChanges(check_value)

    def test_05e_create_table_with_pkey_and_unique_col(self):
        """Unique on a non-PK column becomes a named constraint."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('test_table_with_uniquecol', pkey='code')
        tbl.column('code', size=':30', unique=True)
        tbl.column('description')
        tbl.column('uniquecol', unique=True, size='10')
        check_value = 'CREATE TABLE "alfa"."alfa_test_table_with_uniquecol"(\n "code" character varying(30) NOT NULL,\n "description" text,\n "uniquecol" character(10),\n PRIMARY KEY (code)\n);\nALTER TABLE "alfa"."alfa_test_table_with_uniquecol"\nADD CONSTRAINT "cst_9bbd2120" UNIQUE ("uniquecol");'
        self.checkChanges(check_value)

    def test_06_prepare_table(self):
        pkg = self.src.package('alfa')
        tbl = pkg.table('recipe_row_annotation', pkey='id')
        tbl.column('id', dtype='serial')
        tbl.column('description')
        tbl.column('recipe_code', size=':12').relation('alfa.recipe.code', mode='foreignkey')
        tbl.column('recipe_line', dtype='L')
        tbl.compositeColumn('recipe_row_reference', columns='recipe_code,recipe_line')

        tbl = pkg.table('author', pkey='id')
        tbl.column('id', dtype='serial')
        tbl.column('name', size=':45', unique=True)
        tbl.column('tax_code', size=':45')
        self.checkChanges(apply_only=True)

    def test_06a_add_relation_to_pk_single(self):
        """FK to a single-column primary key."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('recipe_row')
        tbl.column('recipe_code').relation('alfa.recipe.code', mode='foreignkey')
        self.checkChanges('ALTER TABLE "alfa"."alfa_recipe_row" \nADD CONSTRAINT "fk_04a64b2e" FOREIGN KEY ("recipe_code") REFERENCES "alfa"."alfa_recipe" ("code") ON UPDATE CASCADE;')

    def test_06b_add_relation_to_pk_multi(self):
        pkg = self.src.package('alfa')
        tbl = pkg.table('recipe_row_annotation')
        tbl.compositeColumn('recipe_row_reference').relation(
            'alfa.recipe_row.composite_key', mode='foreignkey'
        )
        check_changes = 'CREATE INDEX idx_3e9365a8 ON "alfa"."alfa_recipe_row_annotation" USING btree ("recipe_code", "recipe_line");\nALTER TABLE "alfa"."alfa_recipe_row_annotation"\n ADD CONSTRAINT "fk_cbe2056f" FOREIGN KEY ("recipe_code", "recipe_line") REFERENCES "alfa"."alfa_recipe_row" ("recipe_code", "recipe_line") ON UPDATE CASCADE;'
        self.checkChanges(check_changes)

    def test_06c_add_relation_to_nopk_single(self):
        pkg = self.src.package('alfa')
        tbl = pkg.table('recipe')
        tbl.column('author_name', size=':44').relation('alfa.author.name',
                                                       mode='foreignkey')
        check_changes = 'ALTER TABLE "alfa"."alfa_recipe"\nADD COLUMN "author_name" character varying(44) ;\nCREATE INDEX idx_44a37a95 ON "alfa"."alfa_recipe" USING btree ("author_name");\nALTER TABLE "alfa"."alfa_recipe"\n ADD CONSTRAINT "fk_7f18eae7" FOREIGN KEY ("author_name") REFERENCES "alfa"."alfa_author" ("name") ON UPDATE CASCADE;'
        self.checkChanges(check_changes)

    def test_06d_add_relation_to_nopk_multi(self):
        pkg = self.src.package('alfa')
        tbl = pkg.table('recipe')
        tbl.column('restaurant_vat', size=':30')
        tbl.column('restaurant_country', size='2')

        tbl.compositeColumn('restaurant_ref', columns='restaurant_country,restaurant_vat'
                            ).relation('alfa.restaurant.international_vat', mode='foreignkey')
        check_changes = 'ALTER TABLE "alfa"."alfa_recipe"\nADD COLUMN "restaurant_vat" character varying(30) ,\nADD COLUMN "restaurant_country" character(2) ;\nCREATE INDEX idx_f7e554d6 ON "alfa"."alfa_recipe" USING btree ("restaurant_country", "restaurant_vat");\nALTER TABLE "alfa"."alfa_recipe"\n ADD CONSTRAINT "fk_8e2e04f3" FOREIGN KEY ("restaurant_country", "restaurant_vat") REFERENCES "alfa"."alfa_restaurant" ("country", "vat_number") ON UPDATE CASCADE;'
        self.checkChanges(check_changes)

    def test_06e_add_relation_to_pk_single_onDelete_setnull_deferred(self):
        """FK with onDelete_sql='setnull' implies DEFERRABLE INITIALLY DEFERRED."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('recipe')
        tbl.column('company_code').relation('alfa.company.code', mode='foreignkey',
                                            onDelete_sql='setnull')
        check_value = 'ALTER TABLE "alfa"."alfa_recipe"\nADD COLUMN "company_code" text ;\nCREATE INDEX idx_6cbb7b70 ON "alfa"."alfa_recipe" USING btree ("company_code");\nALTER TABLE "alfa"."alfa_recipe"\n ADD CONSTRAINT "fk_f87f3ff6" FOREIGN KEY ("company_code") REFERENCES "alfa"."alfa_company" ("code") ON DELETE SET NULL ON UPDATE CASCADE DEFERRABLE INITIALLY DEFERRED;'
        self.checkChanges(check_value)

    def test_07a_create_table_with_relation_to_pk_single(self):
        pkg = self.src.package('alfa')
        tbl = pkg.table('product', pkey='id')
        tbl.column('id', dtype='serial')
        tbl.column('description')
        tbl.column('recipe_code').relation('alfa.recipe.code', mode='foreignkey')
        check_value = 'CREATE TABLE "alfa"."alfa_product"(\n "id" serial8 NOT NULL,\n "description" text,\n "recipe_code" text,\n PRIMARY KEY (id)\n);\nCREATE INDEX idx_78fd5e36 ON "alfa"."alfa_product" USING btree ("recipe_code");\nALTER TABLE "alfa"."alfa_product"\n ADD CONSTRAINT "fk_ff154564" FOREIGN KEY ("recipe_code") REFERENCES "alfa"."alfa_recipe" ("code") ON UPDATE CASCADE;'
        self.checkChanges(check_value)

    def test_07b_create_table_with_relation_to_pk_multi(self):
        pkg = self.src.package('alfa')
        tbl = pkg.table('recipe_row_alternative', pkey='id')
        tbl.column('id', dtype='serial')
        tbl.column('description')
        tbl.column('vegan', dtype='B')
        tbl.column('gluten_free', dtype='B')
        tbl.column('recipe_code', size=':12').relation('alfa.recipe.code', mode='foreignkey')
        tbl.column('recipe_line', dtype='L')
        tbl.compositeColumn('recipe_row_reference', columns='recipe_code,recipe_line').relation(
            'alfa.recipe_row.composite_key', mode='foreignkey'
        )
        check_changes = 'CREATE TABLE "alfa"."alfa_recipe_row_alternative"(\n "id" serial8 NOT NULL,\n "description" text,\n "vegan" boolean,\n "gluten_free" boolean,\n "recipe_code" character varying(12),\n "recipe_line" bigint,\n PRIMARY KEY (id)\n);\nCREATE INDEX idx_17fca263 ON "alfa"."alfa_recipe_row_alternative" USING btree ("recipe_code");\nCREATE INDEX idx_bd86c8b3 ON "alfa"."alfa_recipe_row_alternative" USING btree ("recipe_code", "recipe_line");\nALTER TABLE "alfa"."alfa_recipe_row_alternative"\n ADD CONSTRAINT "fk_a2e10c8f" FOREIGN KEY ("recipe_code") REFERENCES "alfa"."alfa_recipe" ("code") ON UPDATE CASCADE;\nALTER TABLE "alfa"."alfa_recipe_row_alternative"\n ADD CONSTRAINT "fk_b03ef3c2" FOREIGN KEY ("recipe_code", "recipe_line") REFERENCES "alfa"."alfa_recipe_row" ("recipe_code", "recipe_line") ON UPDATE CASCADE;'
        self.checkChanges(check_changes)

    def test_08a_modify_column_type(self):
        """Tests modifying the data type of an existing column."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('ingredient')
        tbl.column('description', size=':50')
        check_value = 'ALTER TABLE "alfa"."alfa_ingredient" \n ALTER COLUMN "description" TYPE character varying(50);'
        self.checkChanges(check_value)

    def test_08e_modify_column_from_text_to_bytea(self):
        """Incompatible conversion on empty column: DROP + ADD."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('ingredient')
        foo_varchar = tbl.column('foo_varchar', size=':50')
        self.checkChanges(apply_only=True)
        foo_varchar.attributes['dtype'] = 'O'
        foo_varchar.attributes.pop('size')
        self.checkChanges('ALTER TABLE "alfa"."alfa_ingredient"\nDROP COLUMN "foo_varchar",\nADD COLUMN "foo_varchar" bytea;')

    def test_08b_modify_column_type(self):
        pkg = self.src.package('alfa')
        tbl = pkg.table('recipe_row_alternative')
        tbl.column('vegan').attributes.pop('dtype')
        tbl.column('vegan', size='1', values='Y:Yes,C:Crudist,F:Fresh Fruit')
        # Any type -> text is always convertible: simple ALTER COLUMN TYPE
        check_value = 'ALTER TABLE "alfa"."alfa_recipe_row_alternative"\nALTER COLUMN "vegan" TYPE character(1);'
        self.checkChanges(check_value)

    def test_08c_modify_column_add_unique(self):
        pkg = self.src.package('alfa')
        tbl = pkg.table('author')
        tbl.column('tax_code', unique=True)
        tbl.column('foo')  # added to test the placement of ADD CONSTRAINT
        check_value = 'ALTER TABLE "alfa"."alfa_author"\nADD COLUMN "foo" text ;\nALTER TABLE "alfa"."alfa_author"\nADD CONSTRAINT "cst_99206169" UNIQUE ("tax_code");'
        self.checkChanges(check_value)

    def test_08c_modify_column_remove_unique(self):
        pkg = self.src.package('alfa')
        tbl = pkg.table('author')
        tbl.column('tax_code').attributes.pop('unique')
        check_value = 'ALTER TABLE "alfa"."alfa_author"\nDROP CONSTRAINT IF EXISTS "cst_99206169";'
        self.checkChanges(check_value)

    def test_08d_modify_dtype_bis(self):
        pkg = self.src.package('alfa')
        tbl = pkg.table('author')
        tbl.column('foo', dtype='D')
        # Prudent mode: direct conversion with USING clause (no backup)
        check_value = 'ALTER TABLE "alfa"."alfa_author"\nALTER COLUMN "foo" TYPE date USING CASE WHEN "foo" IS NULL THEN NULL WHEN "foo" ~ \'^[0-9]{4}-[0-9]{2}-[0-9]{2}\' THEN "foo"::date ELSE NULL END;'
        self.checkChanges(check_value)

    def test_09a_remove_column(self):
        pkg = self.src.package('alfa')
        pkg.table('author')['columns'].pop('foo')
        check_value = 'ALTER TABLE "alfa"."alfa_author" \n DROP COLUMN "foo";'
        self.checkChanges(check_value)

    def test_10a_empty_table_creation(self):
        pkg = self.src.package('alfa')
        tbl = pkg.table('my_empty_table')
        self.checkChanges(apply_only=True)
        tbl.attributes.update(pkey='id')
        tbl.column('id', size='22')
        self.checkChanges('CREATE TABLE "alfa"."alfa_my_empty_table"(\n "id" character(22) NOT NULL,\n PRIMARY KEY (id)\n);')

    def test_11_varchar_min_max(self):
        pkg = self.src.package('alfa')
        tbl = pkg.table('text_test_table', pkey='id')
        tbl.column('id', dtype='serial')

        # see #324 - varchar columns with min/max only use the max
        tbl.column('code', dtype='A', size="5:18")
        check_value = 'CREATE TABLE "alfa"."alfa_text_test_table"("id" serial8 NOT NULL, "code" character varying(18), PRIMARY KEY(id));'
        self.checkChanges(check_value)

    def test_12a_simple_text_conversion(self):
        """Simple type conversion without USING clause (T -> A)."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('type_conv_test', pkey='id')
        tbl.column('id', dtype='serial')
        tbl.column('text_col')  # Text column (no size = text)
        check_value = 'CREATE TABLE "alfa"."alfa_type_conv_test"("id" serial8 NOT NULL, "text_col" text, PRIMARY KEY(id));'
        self.checkChanges(check_value)

        # Now change text to varchar (simple conversion)
        tbl.column('text_col', size=':50')
        check_value = 'ALTER TABLE "alfa"."alfa_type_conv_test" \n ALTER COLUMN "text_col" TYPE character varying(50);'
        self.checkChanges(check_value)

    def test_12b_simple_text_to_varchar(self):
        """Simple conversion back (A -> A with different size)."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('type_conv_test', pkey='id')
        tbl.column('text_col', dtype='A', size=':30')
        check_value = 'ALTER TABLE "alfa"."alfa_type_conv_test" \n ALTER COLUMN "text_col" TYPE character varying(30);'
        self.checkChanges(check_value)

    def test_12c_text_to_timestamp_conversion(self):
        """Complex conversion with USING clause (T -> DHZ), prudent mode."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('type_conv_test', pkey='id')
        tbl.column('ts_col')
        self.checkChanges('ALTER TABLE "alfa"."alfa_type_conv_test" \n ADD COLUMN "ts_col" text;', apply_only=True)

        tbl.column('ts_col', dtype='DHZ')
        check_value = 'ALTER TABLE "alfa"."alfa_type_conv_test" \n ALTER COLUMN "ts_col" TYPE timestamp with time zone USING CASE WHEN "ts_col" IS NULL THEN NULL WHEN "ts_col" ~ \'^[0-9]{4}-[0-9]{2}-[0-9]{2}\' THEN "ts_col"::timestamp with time zone ELSE NULL END;'
        self.checkChanges(check_value)

    def test_12d_text_to_date_conversion(self):
        """Text to date conversion, prudent mode."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('type_conv_test', pkey='id')
        tbl.column('date_col')
        self.checkChanges('ALTER TABLE "alfa"."alfa_type_conv_test" \n ADD COLUMN "date_col" text;', apply_only=True)

        tbl.column('date_col', dtype='D')
        check_value = 'ALTER TABLE "alfa"."alfa_type_conv_test" \n ALTER COLUMN "date_col" TYPE date USING CASE WHEN "date_col" IS NULL THEN NULL WHEN "date_col" ~ \'^[0-9]{4}-[0-9]{2}-[0-9]{2}\' THEN "date_col"::date ELSE NULL END;'
        self.checkChanges(check_value)

    def test_12e_text_to_integer_conversion(self):
        """Text to integer conversion, prudent mode."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('type_conv_test', pkey='id')
        tbl.column('int_col')
        self.checkChanges('ALTER TABLE "alfa"."alfa_type_conv_test" \n ADD COLUMN "int_col" text;', apply_only=True)

        tbl.column('int_col', dtype='I')
        check_value = 'ALTER TABLE "alfa"."alfa_type_conv_test" \n ALTER COLUMN "int_col" TYPE integer USING CASE WHEN "int_col" IS NULL THEN NULL WHEN "int_col" ~ \'^-?[0-9]+$\' THEN "int_col"::integer ELSE NULL END;'
        self.checkChanges(check_value)

    def test_12f_text_to_boolean_conversion(self):
        """Text to boolean conversion, prudent mode."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('type_conv_test', pkey='id')
        tbl.column('bool_col')
        self.checkChanges('ALTER TABLE "alfa"."alfa_type_conv_test" \n ADD COLUMN "bool_col" text;', apply_only=True)

        tbl.column('bool_col', dtype='B')
        check_value = 'ALTER TABLE "alfa"."alfa_type_conv_test" \n ALTER COLUMN "bool_col" TYPE boolean USING CASE WHEN "bool_col" IS NULL THEN NULL WHEN LOWER("bool_col") IN (\'true\', \'t\', \'yes\', \'y\', \'1\') THEN TRUE WHEN LOWER("bool_col") IN (\'false\', \'f\', \'no\', \'n\', \'0\', \'\') THEN FALSE ELSE NULL END;'
        self.checkChanges(check_value)

    def test_12g_text_to_numeric_conversion(self):
        """Text to numeric conversion, prudent mode."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('type_conv_test', pkey='id')
        tbl.column('num_col')
        self.checkChanges('ALTER TABLE "alfa"."alfa_type_conv_test" \n ADD COLUMN "num_col" text;', apply_only=True)

        tbl.column('num_col', dtype='N', size='10,2')
        check_value = 'ALTER TABLE "alfa"."alfa_type_conv_test" \n ALTER COLUMN "num_col" TYPE numeric(10,2) USING CASE WHEN "num_col" IS NULL THEN NULL WHEN "num_col" ~ \'^-?[0-9]+(\\.[0-9]+)?$\' THEN "num_col"::numeric ELSE NULL END;'
        self.checkChanges(check_value)

    def test_12h_real_to_integer_conversion(self):
        """Real to integer conversion with rounding, prudent mode."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('type_conv_test', pkey='id')
        tbl.column('real_col', dtype='R')
        self.checkChanges('ALTER TABLE "alfa"."alfa_type_conv_test" \n ADD COLUMN "real_col" real;', apply_only=True)

        tbl.column('real_col', dtype='I')
        check_value = 'ALTER TABLE "alfa"."alfa_type_conv_test" \n ALTER COLUMN "real_col" TYPE integer USING CASE WHEN "real_col" IS NULL THEN NULL ELSE ROUND("real_col")::integer END;'
        self.checkChanges(check_value)

    def test_12i_incompatible_conversion_empty_column(self):
        """Incompatible conversion on empty column: drop and recreate."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('type_conv_test', pkey='id')
        tbl.column('bytea_col')
        self.checkChanges('ALTER TABLE "alfa"."alfa_type_conv_test" \n ADD COLUMN "bytea_col" text;', apply_only=True)

        tbl.column('bytea_col', dtype='O')
        check_value = 'ALTER TABLE "alfa"."alfa_type_conv_test"\nDROP COLUMN "bytea_col",\nADD COLUMN "bytea_col" bytea;'
        self.checkChanges(check_value)

    def test_12j_date_conversions(self):
        """Date/time type conversions."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('type_conv_test', pkey='id')
        tbl.column('date_time_col', dtype='D')  # Start with date
        self.checkChanges('ALTER TABLE "alfa"."alfa_type_conv_test" \n ADD COLUMN "date_time_col" date;', apply_only=True)

        # Convert to timestamp (simple conversion)
        tbl.column('date_time_col', dtype='DH')
        check_value = 'ALTER TABLE "alfa"."alfa_type_conv_test" \n ALTER COLUMN "date_time_col" TYPE timestamp without time zone;'
        self.checkChanges(check_value, apply_only=True)

        # Convert to timestamp with timezone (simple conversion)
        tbl.column('date_time_col', dtype='DHZ')
        check_value = 'ALTER TABLE "alfa"."alfa_type_conv_test" \n ALTER COLUMN "date_time_col" TYPE timestamp with time zone;'
        self.checkChanges(check_value)

    def test_12k_numeric_conversions(self):
        """Numeric type conversions."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('type_conv_test', pkey='id')
        tbl.column('numeric_col', dtype='I')  # Start with integer
        self.checkChanges('ALTER TABLE "alfa"."alfa_type_conv_test" \n ADD COLUMN "numeric_col" integer;', apply_only=True)

        # Convert to bigint (simple conversion)
        tbl.column('numeric_col', dtype='L')
        check_value = 'ALTER TABLE "alfa"."alfa_type_conv_test" \n ALTER COLUMN "numeric_col" TYPE bigint;'
        self.checkChanges(check_value, apply_only=True)

        # Convert to numeric (simple conversion)
        tbl.column('numeric_col', dtype='N', size='14,2')
        check_value = 'ALTER TABLE "alfa"."alfa_type_conv_test" \n ALTER COLUMN "numeric_col" TYPE numeric(14,2);'
        self.checkChanges(check_value)

    def test_12l_any_to_text_integer(self):
        """Generic conversion: integer to text."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('to_text_test', pkey='id')
        tbl.column('id', dtype='serial')
        tbl.column('int_col', dtype='I')
        self.checkChanges('CREATE TABLE "alfa"."alfa_to_text_test"("id" serial8 NOT NULL, "int_col" integer, PRIMARY KEY(id));', apply_only=True)

        tbl.column('int_col', dtype='T')
        check_value = 'ALTER TABLE "alfa"."alfa_to_text_test" \n ALTER COLUMN "int_col" TYPE text;'
        self.checkChanges(check_value)

    def test_12m_any_to_text_date(self):
        """Generic conversion: date to text."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('to_text_test', pkey='id')
        tbl.column('date_col', dtype='D')
        self.checkChanges('ALTER TABLE "alfa"."alfa_to_text_test" \n ADD COLUMN "date_col" date;', apply_only=True)

        tbl.column('date_col', dtype='T')
        check_value = 'ALTER TABLE "alfa"."alfa_to_text_test" \n ALTER COLUMN "date_col" TYPE text;'
        self.checkChanges(check_value)

    def test_12n_any_to_text_boolean(self):
        """Generic conversion: boolean to text."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('to_text_test', pkey='id')
        tbl.column('bool_col', dtype='B')
        self.checkChanges('ALTER TABLE "alfa"."alfa_to_text_test" \n ADD COLUMN "bool_col" boolean;', apply_only=True)

        tbl.column('bool_col', dtype='T')
        check_value = 'ALTER TABLE "alfa"."alfa_to_text_test" \n ALTER COLUMN "bool_col" TYPE text;'
        self.checkChanges(check_value)

    def test_12o_any_to_text_timestamp(self):
        """Generic conversion: timestamp with timezone to text."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('to_text_test', pkey='id')
        tbl.column('ts_col', dtype='DHZ')
        self.checkChanges('ALTER TABLE "alfa"."alfa_to_text_test" \n ADD COLUMN "ts_col" timestamp with time zone;', apply_only=True)

        tbl.column('ts_col', dtype='T')
        check_value = 'ALTER TABLE "alfa"."alfa_to_text_test" \n ALTER COLUMN "ts_col" TYPE text;'
        self.checkChanges(check_value)

    def test_12p_any_to_text_numeric(self):
        """Generic conversion: numeric to varchar."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('to_text_test', pkey='id')
        tbl.column('num_col', dtype='N', size='10,2')
        self.checkChanges('ALTER TABLE "alfa"."alfa_to_text_test" \n ADD COLUMN "num_col" numeric(10,2);', apply_only=True)

        tbl.column('num_col', dtype='A', size=':50')
        check_value = 'ALTER TABLE "alfa"."alfa_to_text_test" \n ALTER COLUMN "num_col" TYPE character varying(50);'
        self.checkChanges(check_value)

    def test_12q_bytea_to_text(self):
        """Bytea to text conversion with encode."""
        pkg = self.src.package('alfa')
        tbl = pkg.table('to_text_test', pkey='id')
        tbl.column('bytea_col', dtype='O')
        self.checkChanges('ALTER TABLE "alfa"."alfa_to_text_test" \n ADD COLUMN "bytea_col" bytea;', apply_only=True)

        tbl.column('bytea_col', dtype='T')
        check_value = "ALTER TABLE \"alfa\".\"alfa_to_text_test\" \n ALTER COLUMN \"bytea_col\" TYPE text USING encode(\"bytea_col\", 'hex');"
        self.checkChanges(check_value)
