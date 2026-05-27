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
# MAGIC A UDF that returns BOOLEAN per row, attached to one table at a time. The legacy approach has no policy-level audience controls, so the UDF must do all of: (a) decide *who is in scope*, and (b) decide *which rows that user can see*. Note how access control and row routing are tangled together.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Legacy: UDF has to handle both "who can use this" and "which row matches".
# MAGIC -- Compare with the ABAC version below where TO/EXCEPT in the policy handles (a).
# MAGIC CREATE OR REPLACE FUNCTION region_filter(region_col STRING)
# MAGIC RETURNS BOOLEAN
# MAGIC RETURN
# MAGIC   is_account_group_member('pii-readers')                                    -- privileged readers see all rows
# MAGIC   OR (is_account_group_member('analysts-east') AND region_col = 'east')     -- east analysts see east rows
# MAGIC   OR (is_account_group_member('analysts-west') AND region_col = 'west');    -- west analysts see west rows
# MAGIC
# MAGIC -- Attach to the transactions table.
# MAGIC ALTER TABLE transactions SET ROW FILTER region_filter ON (region);

# COMMAND ----------

# MAGIC %sql
# MAGIC -- As workspace admin, you see all regions. Run this query as `analysts-east` (incognito, second user)
# MAGIC -- to see only east rows.
# MAGIC SELECT region, count(*) AS rows_visible
# MAGIC FROM transactions
# MAGIC GROUP BY region
# MAGIC ORDER BY region;

# COMMAND ----------

# MAGIC %md
# MAGIC ### The same problem as masks
# MAGIC
# MAGIC We attached the filter to `transactions`. We also need it on `customers` (also has `region`), on `support_notes` once we add a region join, on any future table that ever gets a `region` column…
# MAGIC
# MAGIC Detach and switch to ABAC.

# COMMAND ----------

# MAGIC %sql
# MAGIC ALTER TABLE transactions DROP ROW FILTER;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Approach B — ABAC `CREATE POLICY ... ROW FILTER`
# MAGIC
# MAGIC One policy. Applies to every table that has a column tagged `demo_row_scope = 'region'`. Tag a new table's region column → filtered automatically.
# MAGIC
# MAGIC ### Step 1 — add a second governed tag for row-scope identifiers
# MAGIC
# MAGIC > **Account-level reminder** — this `CREATE GOVERNED TAG` will fail if the tag already exists in the account. Use a unique name per demo or have an account admin drop it first.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE GOVERNED TAG demo_row_scope
# MAGIC   DESCRIPTION 'Marks the column that should be used for row-level scoping (uc-governance-demo)'
# MAGIC   VALUES ('region');
# MAGIC
# MAGIC -- Tag the region column on every table that has one.
# MAGIC ALTER TABLE customers    ALTER COLUMN region SET TAGS ('demo_row_scope' = 'region');
# MAGIC ALTER TABLE transactions ALTER COLUMN region SET TAGS ('demo_row_scope' = 'region');

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 2 — the row filter UDF
# MAGIC
# MAGIC Unlike column masks, the row-filter UDF *does* need group logic — because "east analysts see east rows, west analysts see west rows" is a per-row attribute match that can't be expressed in the policy's `TO/EXCEPT` clauses. But notice we're **only** doing routing here, not access control. The `TO/EXCEPT` clauses in the policy below still handle "who is in scope at all."
# MAGIC
# MAGIC The parameter name must match the alias declared in the policy's `MATCH COLUMNS ... AS` clause.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Pure row-routing logic. No "privileged readers see all" line — that case is handled by
# MAGIC -- the policy's `EXCEPT pii-readers` clause (the UDF isn't even called for them).
# MAGIC CREATE OR REPLACE FUNCTION region_filter_abac(region STRING)
# MAGIC RETURNS BOOLEAN
# MAGIC RETURN
# MAGIC   (is_account_group_member('analysts-east') AND region = 'east')
# MAGIC   OR (is_account_group_member('analysts-west') AND region = 'west');

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 3 — the ABAC policy
# MAGIC
# MAGIC Note the differences from `COLUMN MASK`:
# MAGIC - Uses `USING COLUMNS (...)` (not `ON COLUMN`) to pass the matched column to the UDF
# MAGIC - The `TO` clause comes before `FOR TABLES`
# MAGIC
# MAGIC As with the mask policies, `EXCEPT \`pii-readers\`` lets privileged users bypass the row filter entirely (see all rows). The UDF handles routing only.

# COMMAND ----------

dbutils.widgets.text("group_pii_readers", "pii-readers", "PII readers group (account-level)")
pii_readers = dbutils.widgets.get("group_pii_readers")

policy_sql = f"""
CREATE POLICY region_row_filter ON CATALOG `{catalog}`
COMMENT 'Restrict rows to the analyst region. Driven by demo_row_scope=region tag. Bypassed for pii-readers.'
ROW FILTER region_filter_abac
TO `account users` EXCEPT `{pii_readers}`
FOR TABLES MATCH COLUMNS has_tag_value('demo_row_scope', 'region') AS region
  USING COLUMNS (region)
"""
def drop_policy_if_exists(name, target):
    try:
        spark.sql(f"DROP POLICY {name} ON {target}")
    except Exception as e:
        if "POLICY_NOT_FOUND" not in str(e):
            raise

drop_policy_if_exists("region_row_filter", f"CATALOG `{catalog}`")
spark.sql(policy_sql)
print("Policy region_row_filter created.")

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

# drop_policy_if_exists("region_row_filter", f"CATALOG `{catalog}`")
# spark.sql("DROP FUNCTION IF EXISTS region_filter")
# spark.sql("DROP FUNCTION IF EXISTS region_filter_abac")
# spark.sql("ALTER TABLE customers    ALTER COLUMN region UNSET TAGS ('demo_row_scope')")
# spark.sql("ALTER TABLE transactions ALTER COLUMN region UNSET TAGS ('demo_row_scope')")
# spark.sql("DROP GOVERNED TAG demo_row_scope")  # requires account admin
