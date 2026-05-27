# Databricks notebook source
# MAGIC %md
# MAGIC # 03 — Column Masks: Old Way vs. New Way (ABAC)
# MAGIC
# MAGIC This is the **headline** notebook for the "how do I scale governance?" conversation.
# MAGIC
# MAGIC | Approach | How it scales |
# MAGIC |----------|---------------|
# MAGIC | **Legacy** — `ALTER TABLE … SET MASK fn()` | One DDL per column. 50 PII columns × 30 tables = 1,500 statements to maintain. |
# MAGIC | **ABAC** — `CREATE POLICY … COLUMN MASK ON COLUMN TAG('sensitivity_level','pii')` | One policy. Applies automatically to every column tagged `pii`, today and in the future. |
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
# MAGIC -- 1. Write a masking UDF.
# MAGIC --    GOTCHA: the parameter must match the column type. Members of pii-readers see the original; everyone else sees masked.
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
# MAGIC -- Verify the mask works for the current user. As an admin/account-admins member you'll likely see the original;
# MAGIC -- the demo lands harder when run as a non-admin teammate or after temporarily removing yourself from account admins.
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
# MAGIC One policy. It looks at the governed tag (`sensitivity_level`) and applies wherever it's set to `pii`. New PII columns get protected the moment they're tagged.
# MAGIC
# MAGIC ### The masking UDFs
# MAGIC
# MAGIC One per data type we want to mask. Note the parameter name `column_value` — this is the **required** placeholder name when used by an ABAC policy.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE FUNCTION mask_pii_string(column_value STRING)
# MAGIC RETURNS STRING
# MAGIC RETURN CASE
# MAGIC   WHEN is_account_group_member('pii-readers') THEN column_value
# MAGIC   ELSE '***REDACTED***'
# MAGIC END;
# MAGIC
# MAGIC CREATE OR REPLACE FUNCTION mask_pii_date(column_value DATE)
# MAGIC RETURNS DATE
# MAGIC RETURN CASE
# MAGIC   WHEN is_account_group_member('pii-readers') THEN column_value
# MAGIC   ELSE CAST(date_trunc('year', column_value) AS DATE)   -- coarsen DOB to year
# MAGIC END;

# COMMAND ----------

# MAGIC %md
# MAGIC ### The ABAC policy
# MAGIC
# MAGIC Hits every column tagged `sensitivity_level = 'pii'`, applying the right UDF based on data type.

# COMMAND ----------

# MAGIC %sql
# MAGIC DROP POLICY IF EXISTS mask_all_pii_strings ON CATALOG demos;
# MAGIC
# MAGIC CREATE POLICY mask_all_pii_strings ON CATALOG demos
# MAGIC COMMENT 'Mask any STRING column tagged sensitivity_level=pii'
# MAGIC COLUMN MASK mask_pii_string
# MAGIC FOR TABLES MATCH COLUMNS has_tag_value('sensitivity_level', 'pii') AS c
# MAGIC   ON COLUMN c;
# MAGIC
# MAGIC DROP POLICY IF EXISTS mask_all_pii_dates ON CATALOG demos;
# MAGIC
# MAGIC CREATE POLICY mask_all_pii_dates ON CATALOG demos
# MAGIC COMMENT 'Coarsen any DATE column tagged sensitivity_level=pii'
# MAGIC COLUMN MASK mask_pii_date
# MAGIC FOR TABLES MATCH COLUMNS has_tag_value('sensitivity_level', 'pii') AS c
# MAGIC   ON COLUMN c;

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
# MAGIC -- support_notes.note_text and .agent_name are also tagged 'pii' — same policy hits them.
# MAGIC SELECT note_id, customer_id, agent_name, note_text FROM support_notes LIMIT 5;

# COMMAND ----------

# MAGIC %md
# MAGIC ## The "one new column" test
# MAGIC
# MAGIC Add a column. Tag it `pii`. The policy picks it up automatically — no new DDL, no ALTER.

# COMMAND ----------

# MAGIC %sql
# MAGIC ALTER TABLE customers ADD COLUMN passport_number STRING;
# MAGIC UPDATE customers SET passport_number = concat('P', cast(customer_id * 17 AS STRING));
# MAGIC SET TAG sensitivity_level = 'pii' ON COLUMN customers.passport_number;
# MAGIC
# MAGIC -- The new column is masked immediately — the policy already covered it.
# MAGIC SELECT customer_id, full_name, passport_number FROM customers LIMIT 5;

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

# MAGIC %sql
# MAGIC -- DROP POLICY IF EXISTS mask_all_pii_strings ON CATALOG demos;
# MAGIC -- DROP POLICY IF EXISTS mask_all_pii_dates   ON CATALOG demos;
# MAGIC -- DROP FUNCTION IF EXISTS mask_pii_string;
# MAGIC -- DROP FUNCTION IF EXISTS mask_pii_date;
# MAGIC -- DROP FUNCTION IF EXISTS mask_email_legacy;
# MAGIC -- ALTER TABLE customers DROP COLUMN passport_number;
