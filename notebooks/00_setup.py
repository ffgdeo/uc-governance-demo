# Databricks notebook source
# MAGIC %md
# MAGIC # 00 — Setup
# MAGIC
# MAGIC Creates the catalog and schema for the demo, and verifies the demo personas (groups) exist.
# MAGIC
# MAGIC **What this notebook does**
# MAGIC 1. Reads catalog/schema/group names from widgets
# MAGIC 2. Creates the catalog and schema (idempotent — safe to re-run)
# MAGIC 3. Checks that the demo groups exist and warns if any are missing
# MAGIC
# MAGIC **Prereqs**: account/workspace admin to create the catalog. Groups must be created via Account Console (this notebook only checks for them).

# COMMAND ----------

dbutils.widgets.text("catalog", "demos", "Catalog")
dbutils.widgets.text("schema", "uc_governance", "Schema")
dbutils.widgets.text("group_stewards", "data-stewards", "Stewards group")
dbutils.widgets.text("group_pii_readers", "pii-readers", "PII readers group")
dbutils.widgets.text("group_analysts_east", "analysts-east", "East analysts group")
dbutils.widgets.text("group_analysts_west", "analysts-west", "West analysts group")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
fq_schema = f"`{catalog}`.`{schema}`"

# COMMAND ----------

# MAGIC %md
# MAGIC ## Create catalog and schema

# COMMAND ----------

spark.sql(f"CREATE CATALOG IF NOT EXISTS `{catalog}` COMMENT 'UC governance demo'")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {fq_schema} COMMENT 'UC governance demo — PII data, tags, masks, filters'")
spark.sql(f"USE CATALOG `{catalog}`")
spark.sql(f"USE SCHEMA `{schema}`")
print(f"Ready: {fq_schema}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Check demo personas (groups)
# MAGIC
# MAGIC Groups must already exist (created in Account Console → User management). This is a soft check — missing groups log a warning so the rest of the demo still runs.

# COMMAND ----------

from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
expected_groups = [
    dbutils.widgets.get("group_stewards"),
    dbutils.widgets.get("group_pii_readers"),
    dbutils.widgets.get("group_analysts_east"),
    dbutils.widgets.get("group_analysts_west"),
]

existing = {g.display_name for g in w.groups.list()}
missing = [g for g in expected_groups if g not in existing]
present = [g for g in expected_groups if g in existing]

print("Present:", ", ".join(present) if present else "(none)")
if missing:
    print("Missing — create these in the Account Console before the demo:")
    for g in missing:
        print(f"  • {g}")
else:
    print("All demo groups present.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## What's next
# MAGIC
# MAGIC Continue to **`01_generate_data`** to populate the schema with synthetic PII data.

# COMMAND ----------

# MAGIC %md
# MAGIC ### Teardown (optional)
# MAGIC
# MAGIC Uncomment to drop everything. Detach ABAC policies first (run teardowns in notebooks 03–06).

# COMMAND ----------

# spark.sql(f"DROP SCHEMA IF EXISTS {fq_schema} CASCADE")
# spark.sql(f"DROP CATALOG IF EXISTS `{catalog}` CASCADE")
