# Databricks notebook source
# MAGIC %md
# MAGIC # 03 — Column Masks: Old Way vs. New Way (ABAC)
# MAGIC
# MAGIC This is the **headline** notebook for the "how do I scale governance?" conversation.
# MAGIC
# MAGIC | Approach | How it scales |
# MAGIC |----------|---------------|
# MAGIC | **Legacy** — `ALTER TABLE … SET MASK fn` | One DDL per column. 50 PII columns × 30 tables = 1,500 statements to maintain. |
# MAGIC | **ABAC** — `CREATE POLICY … COLUMN MASK … MATCH COLUMNS has_tag_value('demo_sensitivity','pii')` | One policy. Applies automatically to every column tagged `pii`, today and in the future. |
# MAGIC
# MAGIC The data model is identical. The maintenance story is night-and-day.

# COMMAND ----------

dbutils.widgets.text("catalog", "demos", "Catalog")
dbutils.widgets.text("schema", "uc_governance", "Schema")
dbutils.widgets.text("group_pii_readers", "pii-readers", "PII readers group")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
pii_readers = dbutils.widgets.get("group_pii_readers")

spark.sql(f"USE CATALOG `{catalog}`")
spark.sql(f"USE SCHEMA `{schema}`")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Approach A — Legacy per-column `SET MASK`
# MAGIC
# MAGIC Define a UDF, attach it to one column on one table.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- 1. Write a masking UDF. Members of `pii-readers` see the original; everyone else sees masked.
# MAGIC CREATE OR REPLACE FUNCTION mask_email_legacy(input_email STRING)
# MAGIC RETURNS STRING
# MAGIC RETURN CASE
# MAGIC   WHEN is_account_group_member('pii-readers') THEN input_email
# MAGIC   ELSE regexp_replace(input_email, '^[^@]+', '****')
# MAGIC END;
# MAGIC
# MAGIC -- 2. Attach the UDF to the email column. This is per-column, per-table.
# MAGIC ALTER TABLE customers ALTER COLUMN email SET MASK mask_email_legacy;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Verify the mask works for the current user. As an admin/account-admins member you'll likely see
# MAGIC -- the original; the demo lands harder when run as a non-admin teammate or after temporarily removing
# MAGIC -- yourself from account admins.
# MAGIC SELECT customer_id, full_name, email FROM customers LIMIT 5;

# COMMAND ----------

# MAGIC %md
# MAGIC ### The problem with this approach
# MAGIC
# MAGIC We only masked `email`. We still need:
# MAGIC - `customers.ssn` → another UDF, another `ALTER TABLE`
# MAGIC - `customers.phone` → another UDF, another `ALTER TABLE`
# MAGIC - `customers.credit_card` → another UDF, another `ALTER TABLE`
# MAGIC - … and the same on every other table with PII
# MAGIC
# MAGIC When a new PII column is added next quarter, someone has to remember to attach the mask. That "someone has to remember" is where breaches come from.
# MAGIC
# MAGIC **Let's detach the legacy mask and look at the ABAC alternative.**

# COMMAND ----------

# MAGIC %sql
# MAGIC ALTER TABLE customers ALTER COLUMN email DROP MASK;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Approach B — ABAC `CREATE POLICY ... COLUMN MASK`
# MAGIC
# MAGIC One policy. It looks at the governed tag (`demo_sensitivity`) and applies wherever it's set to `pii`. New PII columns get protected the moment they're tagged.
# MAGIC
# MAGIC ### The masking UDF
# MAGIC
# MAGIC `column_value` is the **required** placeholder name when used by an ABAC policy.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE FUNCTION mask_pii_string(column_value STRING)
# MAGIC RETURNS STRING
# MAGIC RETURN CASE
# MAGIC   WHEN is_account_group_member('pii-readers') THEN column_value
# MAGIC   ELSE '***REDACTED***'
# MAGIC END;

# COMMAND ----------

# MAGIC %md
# MAGIC ### The ABAC policy
# MAGIC
# MAGIC Hits every column tagged `demo_sensitivity = 'pii'` across the entire catalog.
# MAGIC
# MAGIC > **Note on the `TO` clause** — ABAC policies require a principal in the `TO` clause. Using `` `account users` `` (the built-in group containing every user) means the policy is *evaluated* for everyone. The UDF itself decides who gets the unmasked value via `is_account_group_member('pii-readers')`. Alternative: `TO \`account users\` EXCEPT \`pii-readers\`` skips the policy entirely for the readers group — slightly cleaner but harder to introspect.

# COMMAND ----------

policy_sql = f"""
CREATE POLICY mask_all_pii_strings ON CATALOG `{catalog}`
COMMENT 'Mask any STRING column tagged demo_sensitivity=pii'
COLUMN MASK mask_pii_string
TO `account users`
FOR TABLES MATCH COLUMNS has_tag_value('demo_sensitivity', 'pii') AS c
  ON COLUMN c
"""
def drop_policy_if_exists(name, target):
    """`DROP POLICY IF EXISTS` is not supported — catch POLICY_NOT_FOUND."""
    try:
        spark.sql(f"DROP POLICY {name} ON {target}")
    except Exception as e:
        if "POLICY_NOT_FOUND" not in str(e):
            raise

drop_policy_if_exists("mask_all_pii_strings", f"CATALOG `{catalog}`")
spark.sql(policy_sql)
print("Policy mask_all_pii_strings created.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Verify — one policy, everything covered
# MAGIC
# MAGIC No additional DDL needed on individual columns.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT customer_id, full_name, email, ssn, phone, dob, region FROM customers LIMIT 5;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- `support_notes.note_text` and `.agent_name` are also tagged `pii` — same policy hits them.
# MAGIC SELECT note_id, customer_id, agent_name, substring(note_text, 1, 80) AS note_preview FROM support_notes LIMIT 5;

# COMMAND ----------

# MAGIC %md
# MAGIC ### A note about data types
# MAGIC
# MAGIC The policy above only applies to **STRING** columns. ABAC currently requires:
# MAGIC
# MAGIC 1. **One mask UDF per data type** — STRING UDFs can't operate on DATE columns, and vice versa.
# MAGIC 2. **No overlapping `MATCH COLUMNS`** — UC rejects queries when two policies match the same column.
# MAGIC
# MAGIC So you can't have one tag (`demo_sensitivity=pii`) and two policies (one for strings, one for dates) targeting it. The workaround is **per-type tag values** — e.g. `demo_sensitivity=pii_string` and `demo_sensitivity=pii_date`, with one policy each. This demo keeps things simple by only masking strings; the same pattern extends to dates, integers, etc.

# COMMAND ----------

# MAGIC %md
# MAGIC ## The "one new column" test
# MAGIC
# MAGIC Add a column. Tag it `pii`. The policy picks it up automatically — no new DDL, no `ALTER`.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- If you're re-running the notebook, untag first so the "before" step shows unmasked values.
# MAGIC ALTER TABLE customers ALTER COLUMN passport_number UNSET TAGS ('demo_sensitivity');

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Add and populate a new sensitive column (idempotent).
# MAGIC ALTER TABLE customers ADD COLUMN IF NOT EXISTS passport_number STRING;
# MAGIC UPDATE customers SET passport_number = concat('P', cast(customer_id * 17 AS STRING));
# MAGIC
# MAGIC -- Before tag: unmasked.
# MAGIC SELECT customer_id, passport_number FROM customers LIMIT 3;

# COMMAND ----------

# MAGIC %sql
# MAGIC ALTER TABLE customers ALTER COLUMN passport_number SET TAGS ('demo_sensitivity' = 'pii');
# MAGIC
# MAGIC -- After tag: masked, immediately — the policy already covered it.
# MAGIC SELECT customer_id, passport_number FROM customers LIMIT 3;

# COMMAND ----------

# MAGIC %md
# MAGIC **That's the punchline.** Tag once, governed everywhere. New PII columns inherit the policy at the moment they're tagged — not weeks later when someone notices.

# COMMAND ----------

# MAGIC %md
# MAGIC ## What's next
# MAGIC
# MAGIC Column masking handles "which *values* you see." **`04_row_filters_abac`** handles "which *rows* you see."

# COMMAND ----------

# MAGIC %md
# MAGIC ### Teardown (optional)

# COMMAND ----------

# drop_policy_if_exists("mask_all_pii_strings", f"CATALOG `{catalog}`")
# spark.sql("DROP FUNCTION IF EXISTS mask_pii_string")
# spark.sql("DROP FUNCTION IF EXISTS mask_email_legacy")
# spark.sql("ALTER TABLE customers DROP COLUMN IF EXISTS passport_number")
