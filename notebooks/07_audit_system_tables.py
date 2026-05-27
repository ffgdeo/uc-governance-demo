# Databricks notebook source
# MAGIC %md
# MAGIC # 07 — Audit & System Tables: Closing the Governance Loop
# MAGIC
# MAGIC Tags and policies are the *enforcement*. System tables are the *evidence*.
# MAGIC
# MAGIC In a customer conversation about governance, the question after "can you mask PII?" is always **"can you prove who saw what?"** — for SOX, GDPR, HIPAA, internal audit, take your pick.
# MAGIC
# MAGIC This notebook shows three queries you'd put on a governance dashboard:
# MAGIC
# MAGIC 1. **Current state** — what's tagged, by what policy, where (information_schema, near-real-time)
# MAGIC 2. **Recent activity** — who applied/changed tags and policies, who queried sensitive tables (system.access.audit, ~15min lag)
# MAGIC 3. **Drift detection** — PII-looking columns that aren't yet tagged

# COMMAND ----------

dbutils.widgets.text("catalog", "demos", "Catalog")
dbutils.widgets.text("schema", "uc_governance", "Schema")
dbutils.widgets.text("days_back", "7", "Days of audit history")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
days_back = int(dbutils.widgets.get("days_back"))

spark.sql(f"USE CATALOG `{catalog}`")
spark.sql(f"USE SCHEMA `{schema}`")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Current state — what's tagged right now

# COMMAND ----------

# MAGIC %md
# MAGIC ### All column tags in this catalog

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT
# MAGIC   table_name,
# MAGIC   column_name,
# MAGIC   tag_name,
# MAGIC   tag_value
# MAGIC FROM system.information_schema.column_tags
# MAGIC WHERE catalog_name = current_catalog()
# MAGIC   AND schema_name  = current_schema()
# MAGIC ORDER BY table_name, column_name, tag_name;

# COMMAND ----------

# MAGIC %md
# MAGIC ### All table tags in this catalog

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT table_name, tag_name, tag_value
# MAGIC FROM system.information_schema.table_tags
# MAGIC WHERE catalog_name = current_catalog()
# MAGIC   AND schema_name  = current_schema();

# COMMAND ----------

# MAGIC %md
# MAGIC ### Tag-coverage summary
# MAGIC
# MAGIC The "% of columns classified" number you'd put on an exec dashboard.

# COMMAND ----------

# MAGIC %sql
# MAGIC WITH all_columns AS (
# MAGIC   SELECT table_name, column_name
# MAGIC   FROM system.information_schema.columns
# MAGIC   WHERE table_catalog = current_catalog()
# MAGIC     AND table_schema  = current_schema()
# MAGIC ),
# MAGIC tagged AS (
# MAGIC   SELECT DISTINCT table_name, column_name
# MAGIC   FROM system.information_schema.column_tags
# MAGIC   WHERE catalog_name = current_catalog()
# MAGIC     AND schema_name  = current_schema()
# MAGIC     AND tag_name = 'sensitivity_level'
# MAGIC )
# MAGIC SELECT
# MAGIC   count(*)                                                                 AS total_columns,
# MAGIC   count(t.column_name)                                                     AS classified_columns,
# MAGIC   round(100.0 * count(t.column_name) / nullif(count(*), 0), 1)             AS pct_classified
# MAGIC FROM all_columns a
# MAGIC LEFT JOIN tagged t USING (table_name, column_name);

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Recent activity — system.access.audit

# COMMAND ----------

# MAGIC %md
# MAGIC ### Tag changes
# MAGIC
# MAGIC Who applied, changed, or removed which tag.

# COMMAND ----------

spark.sql(f"""
SELECT
  event_time,
  user_identity.email AS actor,
  action_name,
  request_params['full_name_arg']  AS target,
  request_params['tag_name']       AS tag_name,
  request_params['tag_value']      AS tag_value
FROM system.access.audit
WHERE event_time > current_date() - INTERVAL {days_back} DAY
  AND action_name IN ('setTags','updateTags','deleteTags',
                      'setGovernedTag','removeGovernedTag',
                      'createGovernedTag','dropGovernedTag')
ORDER BY event_time DESC
LIMIT 100
""").display()

# COMMAND ----------

# MAGIC %md
# MAGIC ### Policy changes
# MAGIC
# MAGIC Who created/dropped/altered an ABAC policy. This is the audit trail you'd want to show a compliance team.

# COMMAND ----------

spark.sql(f"""
SELECT
  event_time,
  user_identity.email AS actor,
  action_name,
  request_params['policy_name']  AS policy_name,
  request_params['target_type']  AS target_type,
  request_params['target_name']  AS target_name
FROM system.access.audit
WHERE event_time > current_date() - INTERVAL {days_back} DAY
  AND action_name IN ('createPolicy','dropPolicy','alterPolicy',
                      'setColumnMask','dropColumnMask',
                      'setRowFilter','dropRowFilter')
ORDER BY event_time DESC
LIMIT 100
""").display()

# COMMAND ----------

# MAGIC %md
# MAGIC ### Reads of tagged tables
# MAGIC
# MAGIC Anyone who queried a table in this schema that has a `sensitivity_level` tag. Pairs nicely with a SIEM export.

# COMMAND ----------

catalog_lower = catalog.lower()
schema_lower = schema.lower()

spark.sql(f"""
WITH sensitive_tables AS (
  SELECT DISTINCT
    concat(catalog_name, '.', schema_name, '.', table_name) AS full_table
  FROM system.information_schema.column_tags
  WHERE catalog_name = '{catalog_lower}'
    AND schema_name  = '{schema_lower}'
    AND tag_name = 'sensitivity_level'
)
SELECT
  a.event_time,
  a.user_identity.email AS actor,
  a.action_name,
  a.request_params['table_full_name'] AS table_full_name
FROM system.access.audit a
JOIN sensitive_tables s
  ON a.request_params['table_full_name'] = s.full_table
WHERE a.event_time > current_date() - INTERVAL {days_back} DAY
  AND a.action_name IN ('getTable','listTables','executeStatement','commandSubmit')
ORDER BY a.event_time DESC
LIMIT 200
""").display()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Drift detection — PII-looking columns that aren't yet tagged
# MAGIC
# MAGIC One of the things `ai_classify` is genuinely useful for: a scheduled job that finds new columns and flags the ones that look like PII but haven't been tagged.

# COMMAND ----------

# MAGIC %sql
# MAGIC WITH untagged_columns AS (
# MAGIC   SELECT table_name, column_name
# MAGIC   FROM system.information_schema.columns
# MAGIC   WHERE table_catalog = current_catalog()
# MAGIC     AND table_schema  = current_schema()
# MAGIC     AND (table_name, column_name) NOT IN (
# MAGIC       SELECT table_name, column_name
# MAGIC       FROM system.information_schema.column_tags
# MAGIC       WHERE catalog_name = current_catalog()
# MAGIC         AND schema_name  = current_schema()
# MAGIC         AND tag_name = 'sensitivity_level'
# MAGIC     )
# MAGIC )
# MAGIC SELECT table_name, column_name
# MAGIC FROM untagged_columns
# MAGIC ORDER BY table_name, column_name;

# COMMAND ----------

# MAGIC %md
# MAGIC The list above is the steward's TODO. A scheduled job pipes each untagged column through `ai_classify` (same pattern as notebook 02) and posts findings to Slack.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Wrap-up — the governance loop
# MAGIC
# MAGIC ```
# MAGIC  Discover  →  Classify  →  Tag  →  Policy  →  Audit  →  (loop)
# MAGIC  ai_classify  steward    SET TAG  ABAC      system.*
# MAGIC ```
# MAGIC
# MAGIC Every step is automatable. The platform handles the enforcement; the governance team handles the policy and the exceptions.
# MAGIC
# MAGIC **That's the demo.**
