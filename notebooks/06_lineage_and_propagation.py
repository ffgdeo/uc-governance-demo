# Databricks notebook source
# MAGIC %md
# MAGIC # 06 — Lineage & Policy Propagation
# MAGIC
# MAGIC The objection you'll hear: *"That's nice for raw tables, but my analysts query views and downstream marts. Do the policies still apply?"*
# MAGIC
# MAGIC Short answer: **yes.** Column masks and row filters are enforced at the source. Anyone querying a view that selects from `customers` sees the same masked data — even if the view doesn't mention masking at all.
# MAGIC
# MAGIC And the governed tags themselves propagate via Unity Catalog lineage, so downstream owners can see what's sensitive without reading the source DDL.

# COMMAND ----------

dbutils.widgets.text("catalog", "demos", "Catalog")
dbutils.widgets.text("schema", "uc_governance", "Schema")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")

spark.sql(f"USE CATALOG `{catalog}`")
spark.sql(f"USE SCHEMA `{schema}`")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1 — build a downstream view
# MAGIC
# MAGIC A pretty typical "analyst-facing" view: customer + transaction rollup with PII included.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE VIEW customer_transaction_summary AS
# MAGIC SELECT
# MAGIC   c.customer_id,
# MAGIC   c.full_name,
# MAGIC   c.email,
# MAGIC   c.region,
# MAGIC   count(t.transaction_id) AS txn_count,
# MAGIC   round(sum(t.amount), 2) AS total_amount,
# MAGIC   max(t.txn_date)         AS last_txn_date
# MAGIC FROM customers c
# MAGIC LEFT JOIN transactions t USING (customer_id)
# MAGIC GROUP BY c.customer_id, c.full_name, c.email, c.region;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2 — query the view
# MAGIC
# MAGIC The column masks and row filter from notebooks 03 + 04 propagate. As an `analysts-east` user you'd see:
# MAGIC - `full_name`, `email` → masked (PII)
# MAGIC - Rows → only `region = 'east'`
# MAGIC - `txn_count`, `total_amount` → unmasked (not tagged)
# MAGIC
# MAGIC The view DDL didn't have to mention any of that.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT * FROM customer_transaction_summary LIMIT 10;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3 — materialize as a table
# MAGIC
# MAGIC Persisted tables are slightly different. When you `CREATE TABLE ... AS SELECT` from a source that has masks/filters, the **data** written reflects what *you* (the writer) could see at write time. The downstream table itself is then independent — it has its own tags and policies.
# MAGIC
# MAGIC This is why you want stewards (with `pii-readers` access) running the ETL, not analysts.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Run as admin/pii-readers so the materialization sees unmasked data.
# MAGIC CREATE OR REPLACE TABLE customer_summary_mart AS
# MAGIC SELECT * FROM customer_transaction_summary;
# MAGIC
# MAGIC -- The new table has columns named full_name, email. They aren't yet tagged —
# MAGIC -- which means the ABAC policy doesn't protect them. This is a real gotcha.
# MAGIC SELECT * FROM customer_summary_mart LIMIT 5;

# COMMAND ----------

# MAGIC %md
# MAGIC ### Inherit tags via lineage-aware retagging
# MAGIC
# MAGIC In practice you do one of three things to keep downstream consistent:
# MAGIC
# MAGIC 1. **Tag the downstream columns explicitly** (most common — same `SET TAG` statements).
# MAGIC 2. **Use a lineage-driven job** that copies tags from upstream columns to downstream ones with matching names.
# MAGIC 3. **Keep PII out of the mart** — drop sensitive columns from the SELECT and only re-introduce them in a controlled view.
# MAGIC
# MAGIC Re-tagging the mart:

# COMMAND ----------

# MAGIC %sql
# MAGIC ALTER TABLE customer_summary_mart ALTER COLUMN full_name SET TAGS ('demo_sensitivity' = 'pii');
# MAGIC ALTER TABLE customer_summary_mart ALTER COLUMN email     SET TAGS ('demo_sensitivity' = 'pii');
# MAGIC ALTER TABLE customer_summary_mart ALTER COLUMN region    SET TAGS ('demo_row_scope'   = 'region');
# MAGIC
# MAGIC -- And now the ABAC policies cover the mart too — automatically.
# MAGIC SELECT * FROM customer_summary_mart LIMIT 5;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4 — see the lineage in `system.access`

# COMMAND ----------

catalog_lower = catalog.lower()
schema_lower = schema.lower()

spark.sql(f"""
SELECT
  source_table_full_name,
  target_table_full_name,
  event_time,
  entity_type
FROM system.access.table_lineage
WHERE target_table_catalog = '{catalog_lower}'
  AND target_table_schema  = '{schema_lower}'
  AND event_time > current_date() - INTERVAL 1 DAY
ORDER BY event_time DESC
LIMIT 50
""").display()

# Note: system.access.table_lineage can take ~15 min to populate after the underlying operation.
# If the result is empty or source_table_full_name is NULL, wait and re-run.

# COMMAND ----------

# MAGIC %md
# MAGIC > System tables can lag by ~15 min. If the lineage query returns empty, give it a few minutes and re-run.

# COMMAND ----------

# MAGIC %md
# MAGIC ## What's next
# MAGIC
# MAGIC **`07_audit_system_tables`** — close the loop with the governance dashboard: who's reading what, who tagged what, which policies are in force.

# COMMAND ----------

# MAGIC %md
# MAGIC ### Teardown (optional)

# COMMAND ----------

# MAGIC %sql
# MAGIC -- DROP TABLE IF EXISTS customer_summary_mart;
# MAGIC -- DROP VIEW  IF EXISTS customer_transaction_summary;
