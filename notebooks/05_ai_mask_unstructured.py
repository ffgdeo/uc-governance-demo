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
# MAGIC That's `ai_mask`. It's a built-in SQL function that uses an LLM to identify PII inside text and replace it with placeholders.
# MAGIC
# MAGIC > **Distinction** — `ai_mask` is a **transformation function**, not an access-control primitive. It runs whenever the query runs. To turn it into access control, wrap it in a view or in a `COLUMN MASK` UDF that calls it conditionally.

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
# MAGIC SELECT note_id, customer_id, note_text
# MAGIC FROM support_notes
# MAGIC LIMIT 5;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2 — apply `ai_mask` directly
# MAGIC
# MAGIC List the PII categories you want masked. Anything matching gets replaced with `[MASKED]` (or `[EMAIL]`, `[PHONE]`, etc. depending on the entity type).

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
# MAGIC The cleanest pattern: a `COLUMN MASK` UDF that calls `ai_mask` only for users without the `pii-readers` role. Then wire it into the same ABAC policy from notebook 03 by tagging `support_notes.note_text` with a more specific value (e.g. `pii_freetext`).

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE FUNCTION mask_pii_freetext(column_value STRING)
# MAGIC RETURNS STRING
# MAGIC RETURN CASE
# MAGIC   WHEN is_account_group_member('pii-readers') THEN column_value
# MAGIC   ELSE ai_mask(
# MAGIC     column_value,
# MAGIC     ARRAY('email', 'phone', 'ssn', 'credit card number', 'street address', 'person name')
# MAGIC   )
# MAGIC END;

# COMMAND ----------

# MAGIC %md
# MAGIC ### Option A — attach it to one column the legacy way
# MAGIC
# MAGIC Simple, demo-friendly. Best when you only have one or two free-text columns.

# COMMAND ----------

# MAGIC %sql
# MAGIC ALTER TABLE support_notes ALTER COLUMN note_text SET MASK mask_pii_freetext;
# MAGIC
# MAGIC SELECT note_id, customer_id, note_text FROM support_notes LIMIT 5;

# COMMAND ----------

# MAGIC %md
# MAGIC ### Option B — extend governed tags to drive it
# MAGIC
# MAGIC If you have many free-text PII columns, add a `pii_freetext` value to the tag policy and write an ABAC policy that applies `mask_pii_freetext` wherever the tag is set.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Detach the legacy mask so the ABAC policy can take over.
# MAGIC ALTER TABLE support_notes ALTER COLUMN note_text DROP MASK;
# MAGIC
# MAGIC -- Recreate the sensitivity tag policy with the new value. (Re-create rather than ALTER, for portability.)
# MAGIC DROP GOVERNED TAG IF EXISTS sensitivity_level;
# MAGIC CREATE GOVERNED TAG sensitivity_level
# MAGIC   DESCRIPTION 'Data sensitivity classification'
# MAGIC   VALUES ('pii', 'pii_freetext', 'financial', 'public');
# MAGIC
# MAGIC -- Re-grant ASSIGN (was wiped by DROP).
# MAGIC GRANT ASSIGN ON GOVERNED TAG sensitivity_level TO `data-stewards`;
# MAGIC
# MAGIC -- Re-apply structured-PII tags from notebook 02.
# MAGIC SET TAG sensitivity_level = 'pii'          ON COLUMN customers.email;
# MAGIC SET TAG sensitivity_level = 'pii'          ON COLUMN customers.ssn;
# MAGIC SET TAG sensitivity_level = 'pii'          ON COLUMN customers.phone;
# MAGIC SET TAG sensitivity_level = 'pii'          ON COLUMN customers.full_name;
# MAGIC SET TAG sensitivity_level = 'pii'          ON COLUMN customers.address;
# MAGIC SET TAG sensitivity_level = 'pii'          ON COLUMN customers.dob;
# MAGIC SET TAG sensitivity_level = 'financial'    ON COLUMN customers.credit_card;
# MAGIC SET TAG sensitivity_level = 'financial'    ON COLUMN transactions.amount;
# MAGIC SET TAG sensitivity_level = 'pii_freetext' ON COLUMN support_notes.note_text;
# MAGIC SET TAG sensitivity_level = 'pii'          ON COLUMN support_notes.agent_name;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- New ABAC policy specifically for free-text PII.
# MAGIC DROP POLICY IF EXISTS mask_pii_freetext_columns ON CATALOG demos;
# MAGIC
# MAGIC CREATE POLICY mask_pii_freetext_columns ON CATALOG demos
# MAGIC COMMENT 'AI-driven masking for free-text columns tagged sensitivity_level=pii_freetext'
# MAGIC COLUMN MASK mask_pii_freetext
# MAGIC FOR TABLES MATCH COLUMNS has_tag_value('sensitivity_level', 'pii_freetext') AS c
# MAGIC   ON COLUMN c;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Querying the same table now applies the ai_mask-backed policy.
# MAGIC SELECT note_id, customer_id, agent_name, note_text FROM support_notes LIMIT 5;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Cost note
# MAGIC
# MAGIC `ai_mask` is an LLM call per row. For a 1M-row table it's slower and more expensive than a regex/UDF. Two ways to manage it:
# MAGIC
# MAGIC 1. **Materialize a masked view.** Run `ai_mask` once into a downstream table; grant readers on that instead of the source.
# MAGIC 2. **Selective**. The `COLUMN MASK` UDF only calls `ai_mask` when the user *isn't* in `pii-readers`. Privileged readers pay no LLM cost.

# COMMAND ----------

# MAGIC %md
# MAGIC ## What's next
# MAGIC
# MAGIC **`06_lineage_and_propagation`** — show that masks and tags follow data through views and downstream tables.

# COMMAND ----------

# MAGIC %md
# MAGIC ### Teardown (optional)

# COMMAND ----------

# MAGIC %sql
# MAGIC -- DROP POLICY IF EXISTS mask_pii_freetext_columns ON CATALOG demos;
# MAGIC -- DROP FUNCTION IF EXISTS mask_pii_freetext;
