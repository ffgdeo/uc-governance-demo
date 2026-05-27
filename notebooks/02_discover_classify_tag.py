# Databricks notebook source
# MAGIC %md
# MAGIC # 02 — Discover, Classify, and Tag
# MAGIC
# MAGIC Two-part story:
# MAGIC
# MAGIC 1. **`ai_classify`** — let the platform tell us which columns look like PII, instead of us combing through them manually.
# MAGIC 2. **Governed Tags** — apply a controlled-vocabulary tag (`demo_sensitivity`) to those columns so downstream policies can act on the classification.
# MAGIC
# MAGIC > **Why governed tags vs. regular tags?** Governed tags have an allowed-value list and live at the account level. Anyone with `APPLY TAG` on a securable can scribble whatever they want into a regular tag — that's fine for documentation, useless for security. Governed tags are auditable, restricted-vocabulary metadata you can safely build policies on top of.
# MAGIC
# MAGIC > **Tag name convention** — we use `demo_sensitivity` (rather than a generic name like `sensitivity_level`) because governed tags live at the **account** level. A generic name will collide with what your customer or another demo already created. Pick a unique name per demo.
# MAGIC
# MAGIC > **Built-in classification tags** — this account may already have `class.email_address`, `class.us_ssn`, `class.credit_card`, etc. Those are system tags from Databricks' built-in PII classification. Run `SHOW GOVERNED TAGS LIKE 'class.*'` to see them — a great talking point.

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
# MAGIC -- Sample a handful of values per column, classify each, and aggregate.
# MAGIC -- In production you'd run this via a job over your full catalog.
# MAGIC -- Note: LIMIT inside UNION ALL branches needs parens; QUALIFY needs an explicit aggregation CTE.
# MAGIC WITH sampled AS (
# MAGIC   (SELECT 'email'       AS column_name, CAST(email       AS STRING) AS sample_value FROM customers LIMIT 20)
# MAGIC   UNION ALL (SELECT 'ssn',         CAST(ssn         AS STRING) FROM customers LIMIT 20)
# MAGIC   UNION ALL (SELECT 'credit_card', CAST(credit_card AS STRING) FROM customers LIMIT 20)
# MAGIC   UNION ALL (SELECT 'phone',       CAST(phone       AS STRING) FROM customers LIMIT 20)
# MAGIC   UNION ALL (SELECT 'full_name',   CAST(full_name   AS STRING) FROM customers LIMIT 20)
# MAGIC   UNION ALL (SELECT 'address',     CAST(address     AS STRING) FROM customers LIMIT 20)
# MAGIC   UNION ALL (SELECT 'city',        CAST(city        AS STRING) FROM customers LIMIT 20)
# MAGIC   UNION ALL (SELECT 'region',      CAST(region      AS STRING) FROM customers LIMIT 20)
# MAGIC   UNION ALL (SELECT 'dob',         CAST(dob         AS STRING) FROM customers LIMIT 20)
# MAGIC   UNION ALL (SELECT 'signup_date', CAST(signup_date AS STRING) FROM customers LIMIT 20)
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
# MAGIC ),
# MAGIC counted AS (
# MAGIC   SELECT column_name, predicted_label, count(*) AS votes
# MAGIC   FROM classified
# MAGIC   GROUP BY column_name, predicted_label
# MAGIC )
# MAGIC SELECT column_name, predicted_label, votes
# MAGIC FROM counted
# MAGIC QUALIFY row_number() OVER (PARTITION BY column_name ORDER BY votes DESC) = 1
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
# MAGIC -- Governed tags live at the ACCOUNT level. CREATE will fail if the tag already exists.
# MAGIC -- If you need to re-run, drop it manually as an account admin: DROP GOVERNED TAG demo_sensitivity
# MAGIC -- (Note: DROP GOVERNED TAG IF EXISTS is not currently supported.)
# MAGIC CREATE GOVERNED TAG demo_sensitivity
# MAGIC   DESCRIPTION 'Data sensitivity classification — drives masking and access policies'
# MAGIC   VALUES ('pii', 'financial', 'public');

# COMMAND ----------

# MAGIC %md
# MAGIC ## Part 3 — Grant tag-apply privilege
# MAGIC
# MAGIC To restrict who can apply this tag, grant `APPLY TAG` on the catalog (or schema/table) to stewards. The grant is on the **target** of the tag, not on the tag itself.
# MAGIC
# MAGIC In a real engagement you'd grant `APPLY TAG ON CATALOG` to your stewards group and revoke broader rights from everyone else.

# COMMAND ----------

stewards_group = dbutils.widgets.get("group_stewards")
spark.sql(f"GRANT APPLY TAG ON CATALOG `{catalog}` TO `{stewards_group}`")
print(f"Granted APPLY TAG on catalog {catalog} → {stewards_group}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Part 4 — Apply tags
# MAGIC
# MAGIC ### Column-level tags on `customers`

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Syntax: ALTER TABLE ... ALTER COLUMN ... SET TAGS ('tag_name' = 'value')
# MAGIC -- (The shorter `SET TAG name = value ON COLUMN tbl.col` syntax exists in some docs but is not currently supported by the SQL parser — use ALTER TABLE.)
# MAGIC ALTER TABLE customers ALTER COLUMN email       SET TAGS ('demo_sensitivity' = 'pii');
# MAGIC ALTER TABLE customers ALTER COLUMN ssn         SET TAGS ('demo_sensitivity' = 'pii');
# MAGIC ALTER TABLE customers ALTER COLUMN phone       SET TAGS ('demo_sensitivity' = 'pii');
# MAGIC ALTER TABLE customers ALTER COLUMN full_name   SET TAGS ('demo_sensitivity' = 'pii');
# MAGIC ALTER TABLE customers ALTER COLUMN address     SET TAGS ('demo_sensitivity' = 'pii');
# MAGIC ALTER TABLE customers ALTER COLUMN credit_card SET TAGS ('demo_sensitivity' = 'financial');
# MAGIC
# MAGIC -- NOTE: we deliberately do NOT tag `dob` (a DATE column) with `pii`. ABAC policies cannot
# MAGIC -- have two masks matching the same column, and a single mask UDF can only handle one data
# MAGIC -- type. To mask DATEs you'd use a separate tag value (e.g. 'pii_date') and a second policy.
# MAGIC -- See notebook 03 for the explanation.

# COMMAND ----------

# MAGIC %md
# MAGIC ### Column-level tags on `transactions` and `support_notes`

# COMMAND ----------

# MAGIC %sql
# MAGIC ALTER TABLE transactions  ALTER COLUMN amount     SET TAGS ('demo_sensitivity' = 'financial');
# MAGIC ALTER TABLE support_notes ALTER COLUMN note_text  SET TAGS ('demo_sensitivity' = 'pii');
# MAGIC ALTER TABLE support_notes ALTER COLUMN agent_name SET TAGS ('demo_sensitivity' = 'pii');

# COMMAND ----------

# MAGIC %md
# MAGIC ### Table-level tag (also valid — applies to the whole table)
# MAGIC
# MAGIC Useful when an entire table is sensitive, not just specific columns.

# COMMAND ----------

# MAGIC %sql
# MAGIC ALTER TABLE transactions SET TAGS ('demo_sensitivity' = 'financial');

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
# MAGIC -- ALTER TABLE customers ALTER COLUMN email UNSET TAGS ('demo_sensitivity');
# MAGIC -- ... (repeat for each tagged column/table)
# MAGIC -- DROP GOVERNED TAG demo_sensitivity;  -- requires account admin; IF EXISTS not supported
