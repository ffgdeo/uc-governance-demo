# Databricks notebook source
# MAGIC %md
# MAGIC # 04 — Row Filters: Old Way vs. ABAC
# MAGIC
# MAGIC Column masks hide *values*. Row filters hide *rows*.
# MAGIC
# MAGIC Use case: each analyst should only see rows for their region. We have `analysts-east` and `analysts-west` groups; the row filter drops rows that don't match.
# MAGIC
# MAGIC As with masks, we'll show both the legacy and ABAC approaches.

# COMMAND ----------

dbutils.widgets.text("catalog", "demos", "Catalog")
dbutils.widgets.text("schema", "uc_governance", "Schema")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")

spark.sql(f"USE CATALOG `{catalog}`")
spark.sql(f"USE SCHEMA `{schema}`")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Approach A — Legacy `SET ROW FILTER`
# MAGIC
# MAGIC A UDF that returns a BOOLEAN per row, attached to one table.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- UDF: returns TRUE if the user is allowed to see this row's region.
# MAGIC -- Admin override + per-region group membership.
# MAGIC CREATE OR REPLACE FUNCTION region_filter(region_col STRING)
# MAGIC RETURNS BOOLEAN
# MAGIC RETURN
# MAGIC   is_account_group_member('account admins')
# MAGIC   OR is_account_group_member('pii-readers')
# MAGIC   OR (is_account_group_member('analysts-east') AND region_col = 'east')
# MAGIC   OR (is_account_group_member('analysts-west') AND region_col = 'west');
# MAGIC
# MAGIC -- Attach to the transactions table.
# MAGIC ALTER TABLE transactions SET ROW FILTER region_filter ON (region);

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Run as an east analyst → only sees region='east'.
# MAGIC -- Run as admin → sees everything.
# MAGIC SELECT region, count(*) AS rows_visible
# MAGIC FROM transactions
# MAGIC GROUP BY region
# MAGIC ORDER BY region;

# COMMAND ----------

# MAGIC %md
# MAGIC ### The same problem as masks
# MAGIC
# MAGIC We attached the filter to `transactions`. We also need it on `customers` (also has a `region` column). And on `support_notes` once we add a region join, and on any future table…
# MAGIC
# MAGIC Detach and switch to ABAC.

# COMMAND ----------

# MAGIC %sql
# MAGIC ALTER TABLE transactions DROP ROW FILTER;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Approach B — ABAC `CREATE POLICY ... ROW FILTER`
# MAGIC
# MAGIC One policy. Applies to every table that has a column tagged `region_scope`. Tag a new table's region column → filtered automatically.
# MAGIC
# MAGIC ### Step 1 — add a second governed tag for row-scope identifiers

# COMMAND ----------

# MAGIC %sql
# MAGIC DROP GOVERNED TAG IF EXISTS row_scope;
# MAGIC
# MAGIC CREATE GOVERNED TAG row_scope
# MAGIC   DESCRIPTION 'Marks the column that should be used for row-level scoping'
# MAGIC   VALUES ('region');
# MAGIC
# MAGIC -- Tag the region column on every table that has one.
# MAGIC SET TAG row_scope = 'region' ON COLUMN customers.region;
# MAGIC SET TAG row_scope = 'region' ON COLUMN transactions.region;

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 2 — the row filter UDF
# MAGIC
# MAGIC Same boolean logic. The parameter name must match the column name pattern declared in the policy's `MATCH COLUMNS` clause.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE FUNCTION region_filter_abac(region STRING)
# MAGIC RETURNS BOOLEAN
# MAGIC RETURN
# MAGIC   is_account_group_member('account admins')
# MAGIC   OR is_account_group_member('pii-readers')
# MAGIC   OR (is_account_group_member('analysts-east') AND region = 'east')
# MAGIC   OR (is_account_group_member('analysts-west') AND region = 'west');

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 3 — the ABAC policy

# COMMAND ----------

# MAGIC %sql
# MAGIC DROP POLICY IF EXISTS region_row_filter ON CATALOG demos;
# MAGIC
# MAGIC CREATE POLICY region_row_filter ON CATALOG demos
# MAGIC COMMENT 'Restrict rows to the analyst region. Driven by row_scope=region tag.'
# MAGIC ROW FILTER region_filter_abac
# MAGIC FOR TABLES MATCH COLUMNS has_tag_value('row_scope', 'region') AS region
# MAGIC   ON COLUMN (region);

# COMMAND ----------

# MAGIC %md
# MAGIC ## Verify — both tables filtered by the same policy

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT 'customers'    AS table_name, region, count(*) AS rows_visible FROM customers    GROUP BY region
# MAGIC UNION ALL
# MAGIC SELECT 'transactions' AS table_name, region, count(*) AS rows_visible FROM transactions GROUP BY region
# MAGIC ORDER BY table_name, region;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Combine with masking — full demo
# MAGIC
# MAGIC At this point, an `analysts-east` user querying `customers` sees:
# MAGIC - **Column masks** from notebook 03 → PII columns redacted
# MAGIC - **Row filter** from this notebook → only `region = 'east'` rows
# MAGIC
# MAGIC No per-table DDL. Two policies, applied via tags, governing the whole catalog.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT customer_id, full_name, email, ssn, region
# MAGIC FROM customers
# MAGIC LIMIT 10;

# COMMAND ----------

# MAGIC %md
# MAGIC ## What's next
# MAGIC
# MAGIC Structured columns are easy — they have a type and a name. What about free-text fields where PII is *inside* the string? See **`05_ai_mask_unstructured`**.

# COMMAND ----------

# MAGIC %md
# MAGIC ### Teardown (optional)

# COMMAND ----------

# MAGIC %sql
# MAGIC -- DROP POLICY IF EXISTS region_row_filter ON CATALOG demos;
# MAGIC -- DROP GOVERNED TAG IF EXISTS row_scope;
# MAGIC -- DROP FUNCTION IF EXISTS region_filter;
# MAGIC -- DROP FUNCTION IF EXISTS region_filter_abac;
