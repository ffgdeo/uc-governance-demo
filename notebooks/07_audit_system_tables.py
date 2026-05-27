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
# MAGIC 1. **Current state** — what's tagged, by what policy, where (`information_schema`, near-real-time)
# MAGIC 2. **Recent activity** — who applied/changed tags and policies, who queried sensitive tables (`system.access.audit`, ~15min lag)
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
# MAGIC     AND tag_name = 'demo_sensitivity'
# MAGIC )
# MAGIC SELECT
# MAGIC   count(*)                                                                 AS total_columns,
# MAGIC   count(t.column_name)                                                     AS classified_columns,
# MAGIC   round(100.0 * count(t.column_name) / nullif(count(*), 0), 1)             AS pct_classified
# MAGIC FROM all_columns a
# MAGIC LEFT JOIN tagged t USING (table_name, column_name);

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Recent activity — `system.access.audit`
# MAGIC
# MAGIC > **Action names vary** by audit-log version. The names below match current internal-audit shapes (createTagPolicy, UpdateTagSubentityAssignments, etc.). If you see no rows, try the broader filter `lower(action_name) LIKE '%tag%'` to discover what your workspace actually emits.
# MAGIC >
# MAGIC > **Lag**: audit log writes lag the action by ~15 min. If a recent change isn't showing up, wait and re-run.

# COMMAND ----------

# MAGIC %md
# MAGIC ### Tag policy changes (governed tag CREATE/DROP)
# MAGIC
# MAGIC Who created, dropped, or modified a governed tag.

# COMMAND ----------

spark.sql(f"""
SELECT
  event_time,
  user_identity.email                        AS actor,
  action_name,
  substring(to_json(request_params), 1, 200) AS request_params
FROM system.access.audit
WHERE event_time > current_date() - INTERVAL {days_back} DAY
  AND action_name IN ('createTagPolicy', 'deleteTagPolicy', 'updateTagPolicy',
                      'legacyCreateTagPolicy', 'legacyDeleteTagPolicy', 'legacyUpdateTagPolicy')
ORDER BY event_time DESC
LIMIT 50
""").display()

# COMMAND ----------

# MAGIC %md
# MAGIC ### Tag assignments (who tagged what)
# MAGIC
# MAGIC `UpdateTagSubentityAssignments` covers columns and `UpdateTagSecurableAssignments` covers tables/schemas/catalogs.

# COMMAND ----------

spark.sql(f"""
SELECT
  event_time,
  user_identity.email                        AS actor,
  action_name,
  substring(to_json(request_params), 1, 300) AS request_params
FROM system.access.audit
WHERE event_time > current_date() - INTERVAL {days_back} DAY
  AND action_name IN ('UpdateTagSubentityAssignments', 'UpdateTagSecurableAssignments',
                      'createTagAssignment', 'updateTagAssignments', 'deleteTagAssignment')
ORDER BY event_time DESC
LIMIT 50
""").display()

# COMMAND ----------

# MAGIC %md
# MAGIC ### ABAC policy changes
# MAGIC
# MAGIC Who created or dropped an ABAC policy. This is the audit trail you'd want to show a compliance team.

# COMMAND ----------

spark.sql(f"""
SELECT
  event_time,
  user_identity.email                        AS actor,
  action_name,
  substring(to_json(request_params), 1, 300) AS request_params
FROM system.access.audit
WHERE event_time > current_date() - INTERVAL {days_back} DAY
  AND action_name IN ('createPolicy', 'dropPolicy', 'alterPolicy', 'updatePolicy')
ORDER BY event_time DESC
LIMIT 50
""").display()

# COMMAND ----------

# MAGIC %md
# MAGIC ### Reads of tagged tables
# MAGIC
# MAGIC Anyone who queried a table in this schema that has a `demo_sensitivity` tag. Pairs nicely with a SIEM export.

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
    AND tag_name = 'demo_sensitivity'
)
SELECT
  a.event_time,
  a.user_identity.email AS actor,
  a.action_name,
  coalesce(
    a.request_params['table_full_name'],
    a.request_params['full_name_arg']
  ) AS table_full_name
FROM system.access.audit a
JOIN sensitive_tables s
  ON coalesce(a.request_params['table_full_name'], a.request_params['full_name_arg']) = s.full_table
WHERE a.event_time > current_date() - INTERVAL {days_back} DAY
  AND a.action_name IN ('getTable', 'listTables', 'commandSubmit', 'executeStatement')
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
# MAGIC         AND tag_name = 'demo_sensitivity'
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
