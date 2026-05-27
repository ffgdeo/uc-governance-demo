# Databricks notebook source
# MAGIC %md
# MAGIC # 02 — Discover, Classify, and Tag
# MAGIC
# MAGIC Two-part story:
# MAGIC
# MAGIC 1. **`ai_classify`** — let the platform tell us which columns look like PII, instead of us combing through them manually.
# MAGIC 2. **Governed Tags** — apply a controlled-vocabulary tag (`sensitivity_level`) to those columns so downstream policies can act on the classification.
# MAGIC
# MAGIC > **Why governed tags vs. regular tags?** Governed tags have an allowed-value list and `ASSIGN` privilege. Anyone with permission on the table can scribble whatever they want into a regular tag — that's fine for documentation, useless for security. Governed tags are auditable, restricted-vocabulary metadata you can safely build policies on top of.

# COMMAND ----------

dbutils.widgets.text("catalog", "demos", "Catalog")
dbutils.widgets.text("schema", "uc_governance", "Schema")
dbutils.widgets.text("group_stewards", "data-stewards", "Stewards group")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
stewards = dbutils.widgets.get("group_stewards")

spark.sql(f"USE CATALOG `{catalog}`")
spark.sql(f"USE SCHEMA `{schema}`")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Part 1 — Discover PII with `ai_classify`
# MAGIC
# MAGIC We sample values from every column in `customers`, run them through `ai_classify` with a small label set, and aggregate. Whichever label wins for a column becomes our suggested classification.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Sample a handful of distinct values per column, classify each, and aggregate.
# MAGIC -- This is a demo pattern — in production you'd run it via a job over your full catalog.
# MAGIC WITH sampled AS (
# MAGIC   SELECT 'email'       AS column_name, CAST(email       AS STRING) AS sample_value FROM customers LIMIT 20
# MAGIC   UNION ALL SELECT 'ssn',         CAST(ssn         AS STRING) FROM customers LIMIT 20
# MAGIC   UNION ALL SELECT 'credit_card', CAST(credit_card AS STRING) FROM customers LIMIT 20
# MAGIC   UNION ALL SELECT 'phone',       CAST(phone       AS STRING) FROM customers LIMIT 20
# MAGIC   UNION ALL SELECT 'full_name',   CAST(full_name   AS STRING) FROM customers LIMIT 20
# MAGIC   UNION ALL SELECT 'address',     CAST(address     AS STRING) FROM customers LIMIT 20
# MAGIC   UNION ALL SELECT 'city',        CAST(city        AS STRING) FROM customers LIMIT 20
# MAGIC   UNION ALL SELECT 'region',      CAST(region      AS STRING) FROM customers LIMIT 20
# MAGIC   UNION ALL SELECT 'dob',         CAST(dob         AS STRING) FROM customers LIMIT 20
# MAGIC   UNION ALL SELECT 'signup_date', CAST(signup_date AS STRING) FROM customers LIMIT 20
# MAGIC ),
# MAGIC classified AS (
# MAGIC   SELECT
# MAGIC     column_name,
# MAGIC     ai_classify(
# MAGIC       sample_value,
# MAGIC       ARRAY('email', 'ssn', 'credit_card', 'phone_number', 'full_name', 'address',
# MAGIC             'date_of_birth', 'geographic_region', 'non_pii')
# MAGIC     ) AS predicted_label
# MAGIC   FROM sampled
# MAGIC )
# MAGIC SELECT
# MAGIC   column_name,
# MAGIC   predicted_label,
# MAGIC   count(*) AS votes
# MAGIC FROM classified
# MAGIC GROUP BY column_name, predicted_label
# MAGIC QUALIFY row_number() OVER (PARTITION BY column_name ORDER BY count(*) DESC) = 1
# MAGIC ORDER BY column_name;

# COMMAND ----------

# MAGIC %md
# MAGIC The model should flag `email`, `ssn`, `credit_card`, `phone`, `full_name`, `address`, and `dob` as PII. `region`, `city`, and `signup_date` aren't sensitive by themselves.
# MAGIC
# MAGIC In a real engagement this output becomes a punch list for the data steward — confirm/override, then tag.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Part 2 — Create the governed tag
# MAGIC
# MAGIC One governed tag with three allowed values. We'll use it on columns *and* on tables (e.g. an entire transactions table can be tagged `financial`).

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Drop first if re-running the demo. Detach uses first if any policies reference it.
# MAGIC DROP GOVERNED TAG IF EXISTS sensitivity_level;
# MAGIC
# MAGIC CREATE GOVERNED TAG sensitivity_level
# MAGIC   DESCRIPTION 'Data sensitivity classification — drives masking and access policies'
# MAGIC   VALUES ('pii', 'financial', 'public');

# COMMAND ----------

# MAGIC %md
# MAGIC ## Part 3 — Grant `ASSIGN` to data stewards
# MAGIC
# MAGIC Only stewards can apply this tag. Everyone else sees it on tables (read-only) but can't change it.

# COMMAND ----------

stewards_group = dbutils.widgets.get("group_stewards")
spark.sql(f"GRANT ASSIGN ON GOVERNED TAG sensitivity_level TO `{stewards_group}`")
print(f"Granted ASSIGN on sensitivity_level → {stewards_group}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Part 4 — Apply tags
# MAGIC
# MAGIC ### Column-level tags on `customers`

# COMMAND ----------

# MAGIC %sql
# MAGIC SET TAG sensitivity_level = 'pii' ON COLUMN customers.email;
# MAGIC SET TAG sensitivity_level = 'pii' ON COLUMN customers.ssn;
# MAGIC SET TAG sensitivity_level = 'pii' ON COLUMN customers.phone;
# MAGIC SET TAG sensitivity_level = 'pii' ON COLUMN customers.full_name;
# MAGIC SET TAG sensitivity_level = 'pii' ON COLUMN customers.address;
# MAGIC SET TAG sensitivity_level = 'pii' ON COLUMN customers.dob;
# MAGIC SET TAG sensitivity_level = 'financial' ON COLUMN customers.credit_card;

# COMMAND ----------

# MAGIC %md
# MAGIC ### Column-level tags on `transactions` and `support_notes`

# COMMAND ----------

# MAGIC %sql
# MAGIC SET TAG sensitivity_level = 'financial' ON COLUMN transactions.amount;
# MAGIC SET TAG sensitivity_level = 'pii'       ON COLUMN support_notes.note_text;
# MAGIC SET TAG sensitivity_level = 'pii'       ON COLUMN support_notes.agent_name;

# COMMAND ----------

# MAGIC %md
# MAGIC ### Table-level tag (also valid — applies to the whole table)
# MAGIC
# MAGIC Useful when an entire table is sensitive, not just specific columns.

# COMMAND ----------

# MAGIC %sql
# MAGIC SET TAG sensitivity_level = 'financial' ON TABLE transactions;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Verify — what got tagged

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT
# MAGIC   catalog_name,
# MAGIC   schema_name,
# MAGIC   table_name,
# MAGIC   column_name,
# MAGIC   tag_name,
# MAGIC   tag_value
# MAGIC FROM system.information_schema.column_tags
# MAGIC WHERE catalog_name = current_catalog()
# MAGIC   AND schema_name  = current_schema()
# MAGIC ORDER BY table_name, column_name;

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT
# MAGIC   catalog_name, schema_name, table_name, tag_name, tag_value
# MAGIC FROM system.information_schema.table_tags
# MAGIC WHERE catalog_name = current_catalog()
# MAGIC   AND schema_name  = current_schema();

# COMMAND ----------

# MAGIC %md
# MAGIC ## What's next
# MAGIC
# MAGIC With PII columns tagged, **`03_column_masks_old_vs_new`** shows two ways to mask them — the per-column legacy way vs. a single ABAC policy that hits every PII column in the catalog at once.

# COMMAND ----------

# MAGIC %md
# MAGIC ### Teardown (optional)

# COMMAND ----------

# MAGIC %sql
# MAGIC -- UNSET TAG sensitivity_level ON COLUMN customers.email;
# MAGIC -- ... (repeat for each tagged column/table)
# MAGIC -- DROP GOVERNED TAG IF EXISTS sensitivity_level;
