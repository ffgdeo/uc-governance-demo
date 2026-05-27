# Databricks notebook source
# MAGIC %md
# MAGIC # 05 — `ai_mask` for Unstructured Free-Text
# MAGIC
# MAGIC Column masks protect *structured* columns. But what about a support-call notes field where an agent typed:
# MAGIC
# MAGIC > *"Customer called about a billing issue. Reachable at (555) 123-4567 or alice@example.com. Verified identity with last 4 of SSN 9876."*
# MAGIC
# MAGIC The column itself is just a STRING — there's no column-level mask that can selectively redact the phone, email, and SSN *inside* the text while keeping the rest readable.
# MAGIC
# MAGIC That's `ai_mask`. It's a built-in SQL function that uses an LLM to identify PII inside text and replace it with `[MASKED]`.
# MAGIC
# MAGIC > **Distinction** — `ai_mask` is a **transformation function**, not an access-control primitive. It runs whenever the query runs. To turn it into access control, wrap it in a `COLUMN MASK` UDF that calls it conditionally.

# COMMAND ----------

dbutils.widgets.text("catalog", "demos", "Catalog")
dbutils.widgets.text("schema", "uc_governance", "Schema")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")

spark.sql(f"USE CATALOG `{catalog}`")
spark.sql(f"USE SCHEMA `{schema}`")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1 — see the raw text

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT note_id, customer_id, substring(note_text, 1, 120) AS note_preview
# MAGIC FROM support_notes
# MAGIC LIMIT 5;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2 — apply `ai_mask` directly
# MAGIC
# MAGIC List the PII categories you want masked. Anything matching gets replaced with `[MASKED]`.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT
# MAGIC   note_id,
# MAGIC   customer_id,
# MAGIC   ai_mask(
# MAGIC     note_text,
# MAGIC     ARRAY('email', 'phone', 'ssn', 'credit card number', 'street address', 'person name')
# MAGIC   ) AS note_text_masked
# MAGIC FROM support_notes
# MAGIC LIMIT 5;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3 — make it part of the access-control story
# MAGIC
# MAGIC The cleanest pattern: a `COLUMN MASK` UDF that calls `ai_mask` only for users without the privileged role. Then wire it into an ABAC policy via a more specific tag value.
# MAGIC
# MAGIC ### Step 3a — extend the governed tag with a `pii_freetext` value
# MAGIC
# MAGIC The existing `demo_sensitivity` tag's allowed-value list can't be changed via `ALTER` — we have to drop and recreate. This is a real-world friction point for governed tags: think hard about the value set up front.
# MAGIC
# MAGIC > Re-running this notebook requires an account admin (or the tag's creator). If `DROP GOVERNED TAG` fails with PERMISSION_DENIED, skip the re-create step and just apply tags using whatever values already exist.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Drop the demo_sensitivity tag from note_text BEFORE dropping the tag itself
# MAGIC -- (otherwise some workspaces refuse the drop with REFERENCE_EXISTS).
# MAGIC ALTER TABLE support_notes ALTER COLUMN note_text UNSET TAGS ('demo_sensitivity');
# MAGIC
# MAGIC DROP GOVERNED TAG demo_sensitivity;
# MAGIC
# MAGIC CREATE GOVERNED TAG demo_sensitivity
# MAGIC   DESCRIPTION 'Data sensitivity classification (uc-governance-demo)'
# MAGIC   VALUES ('pii', 'pii_freetext', 'financial', 'public');

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 3b — re-apply all the tags
# MAGIC
# MAGIC Re-creating the governed tag wiped every existing assignment. Re-apply them, with `note_text` now using the new `pii_freetext` value.

# COMMAND ----------

# MAGIC %sql
# MAGIC ALTER TABLE customers ALTER COLUMN email           SET TAGS ('demo_sensitivity' = 'pii');
# MAGIC ALTER TABLE customers ALTER COLUMN ssn             SET TAGS ('demo_sensitivity' = 'pii');
# MAGIC ALTER TABLE customers ALTER COLUMN phone           SET TAGS ('demo_sensitivity' = 'pii');
# MAGIC ALTER TABLE customers ALTER COLUMN full_name       SET TAGS ('demo_sensitivity' = 'pii');
# MAGIC ALTER TABLE customers ALTER COLUMN address         SET TAGS ('demo_sensitivity' = 'pii');
# MAGIC ALTER TABLE customers ALTER COLUMN credit_card     SET TAGS ('demo_sensitivity' = 'financial');
# MAGIC ALTER TABLE customers ALTER COLUMN passport_number SET TAGS ('demo_sensitivity' = 'pii');
# MAGIC ALTER TABLE transactions  ALTER COLUMN amount      SET TAGS ('demo_sensitivity' = 'financial');
# MAGIC ALTER TABLE support_notes ALTER COLUMN agent_name  SET TAGS ('demo_sensitivity' = 'pii');
# MAGIC ALTER TABLE support_notes ALTER COLUMN note_text   SET TAGS ('demo_sensitivity' = 'pii_freetext');

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 3c — the freetext mask UDF and policy
# MAGIC
# MAGIC The UDF calls `ai_mask` for everyone except workspace admins and `pii-readers`. The ABAC policy applies it to any column tagged `pii_freetext`.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE FUNCTION mask_pii_freetext(column_value STRING)
# MAGIC RETURNS STRING
# MAGIC RETURN CASE
# MAGIC   WHEN is_member('admins') OR is_account_group_member('pii-readers') THEN column_value
# MAGIC   ELSE ai_mask(
# MAGIC     column_value,
# MAGIC     ARRAY('email', 'phone', 'ssn', 'credit card number', 'street address', 'person name')
# MAGIC   )
# MAGIC END;

# COMMAND ----------

policy_sql = f"""
CREATE POLICY mask_pii_freetext_columns ON CATALOG `{catalog}`
COMMENT 'AI-driven masking for free-text columns tagged demo_sensitivity=pii_freetext'
COLUMN MASK mask_pii_freetext
TO `account users`
FOR TABLES MATCH COLUMNS has_tag_value('demo_sensitivity', 'pii_freetext') AS c
  ON COLUMN c
"""
spark.sql(f"DROP POLICY IF EXISTS mask_pii_freetext_columns ON CATALOG `{catalog}`")
spark.sql(policy_sql)
print("Policy mask_pii_freetext_columns created.")

# COMMAND ----------

# MAGIC %sql
# MAGIC -- For workspace admins / pii-readers: original text.
# MAGIC -- For everyone else: ai_mask redacts inline.
# MAGIC SELECT note_id, customer_id, agent_name, substring(note_text, 1, 120) AS note_preview
# MAGIC FROM support_notes
# MAGIC LIMIT 5;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Cost note
# MAGIC
# MAGIC `ai_mask` is an LLM call per row. For a 1M-row table it's slower and more expensive than a regex/UDF. Two ways to manage it:
# MAGIC
# MAGIC 1. **Materialize a masked view.** Run `ai_mask` once into a downstream table; grant readers on that instead of the source.
# MAGIC 2. **Selective.** The `COLUMN MASK` UDF only calls `ai_mask` when the user *isn't* in `pii-readers`. Privileged readers pay no LLM cost.

# COMMAND ----------

# MAGIC %md
# MAGIC ## What's next
# MAGIC
# MAGIC **`06_lineage_and_propagation`** — show that masks and tags follow data through views and downstream tables.

# COMMAND ----------

# MAGIC %md
# MAGIC ### Teardown (optional)

# COMMAND ----------

# spark.sql(f"DROP POLICY IF EXISTS mask_pii_freetext_columns ON CATALOG `{catalog}`")
# spark.sql("DROP FUNCTION IF EXISTS mask_pii_freetext")
