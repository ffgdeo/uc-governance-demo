# Databricks notebook source
# MAGIC %md
# MAGIC # 02a — UC Data Classification + System Tags
# MAGIC
# MAGIC **This is the recommended starting point.** Before building anything yourself, turn on the managed UC Data Classification feature — Databricks scans your catalog and tags PII columns with system-governed `class.*` tags automatically.
# MAGIC
# MAGIC | What it does | Where to look |
# MAGIC |--------------|---------------|
# MAGIC | Background AI agent scans every table in the catalog | Catalog Explorer → catalog → **Details** tab → Data Classification |
# MAGIC | Applies system tags: `class.email_address`, `class.us_ssn`, `class.credit_card`, `class.phone_number`, `class.name`, `class.location`, `class.date_of_birth`, `class.us_passport`, etc. | `SHOW GOVERNED TAGS LIKE 'class.*'` |
# MAGIC | Stores raw findings (with sample values + confidence) | `system.data_classification.results` |
# MAGIC | New tables scanned within ~24h of creation | — |
# MAGIC
# MAGIC > **Why this matters**: the customer doesn't have to define their own classification vocabulary, doesn't have to write an `ai_classify` job, doesn't have to maintain it. Tag application is one-click from the **Review** UI. This is the platform doing the work.
# MAGIC >
# MAGIC > Use the custom `demo_sensitivity` tag from notebook 02 for classifications that *don't* map to a built-in category — things like `executive_only`, `q4_earnings`, `trade_secret`. System tags + custom tags work side by side.

# COMMAND ----------

dbutils.widgets.text("catalog", "demos", "Catalog")
dbutils.widgets.text("schema", "uc_governance", "Schema")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")

spark.sql(f"USE CATALOG `{catalog}`")
spark.sql(f"USE SCHEMA `{schema}`")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1 — enable Data Classification (UI, one-time)
# MAGIC
# MAGIC There's no API for this yet (Public Preview). In Catalog Explorer:
# MAGIC
# MAGIC 1. Navigate to your catalog (`fd_serverless_workspace_catalog` in this demo)
# MAGIC 2. Click the **Details** tab
# MAGIC 3. Next to "Data Classification", click **Enable**
# MAGIC 4. Pick which schemas to scan (all by default) → **Save**
# MAGIC 5. The background job kicks off; results typically appear within ~30 min for small catalogs
# MAGIC
# MAGIC The direct UI link for this workspace:
# MAGIC https://fevm-fd-serverless-workspace.cloud.databricks.com/governance/data-classification

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2 — see what got classified

# COMMAND ----------

# MAGIC %sql
# MAGIC -- All findings for this schema, by table + column. confidence = HIGH/MEDIUM/LOW.
# MAGIC SELECT
# MAGIC   table_name,
# MAGIC   column_name,
# MAGIC   class_tag,
# MAGIC   confidence,
# MAGIC   frequency,
# MAGIC   substring(to_json(samples), 1, 200) AS sample_preview
# MAGIC FROM system.data_classification.results
# MAGIC WHERE catalog_name = current_catalog()
# MAGIC   AND schema_name  = current_schema()
# MAGIC   AND class_tag IS NOT NULL
# MAGIC ORDER BY table_name, column_name, class_tag;

# COMMAND ----------

# MAGIC %md
# MAGIC ### What you'd expect to see for this demo's data
# MAGIC
# MAGIC | column | class_tag(s) | confidence |
# MAGIC |--------|--------------|------------|
# MAGIC | `customers.email` | `class.email_address` | HIGH (freq=1.0) |
# MAGIC | `customers.ssn` | `class.us_ssn` | HIGH (freq=1.0) |
# MAGIC | `customers.credit_card` | `class.credit_card` | HIGH |
# MAGIC | `customers.phone` | `class.phone_number` | HIGH |
# MAGIC | `customers.full_name` | `class.name` | HIGH |
# MAGIC | `customers.dob` | `class.date_of_birth` | HIGH |
# MAGIC | `customers.address`, `.city`, `.state`, `.zip_code` | `class.location` | HIGH |
# MAGIC | `support_notes.note_text` | **multiple** (`class.email_address`, `class.phone_number`, `class.credit_card`, `class.name`, `class.location`) | HIGH — because the classifier finds PII *inside* the free text |
# MAGIC | `support_notes.agent_name` | `class.name` | HIGH |
# MAGIC
# MAGIC The free-text column lighting up with multiple PII categories is a great demo moment — that's something custom rules would struggle to do.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3 — apply the suggested tags
# MAGIC
# MAGIC ### Option A — from the UI (recommended for live demos)
# MAGIC
# MAGIC In Catalog Explorer → catalog → **Details** tab → Data Classification panel → **Review**. The UI shows each suggested tag with sample values; click **Accept** to apply.
# MAGIC
# MAGIC This is the demo moment that lands hardest — the customer sees the platform proactively offering tags, the steward clicking accept, and the tag immediately becoming queryable in `information_schema.column_tags`.
# MAGIC
# MAGIC ### Option B — apply programmatically from the results table
# MAGIC
# MAGIC Useful for bulk apply or to script the review-and-accept flow. We'll apply every HIGH-confidence finding.

# COMMAND ----------

high_confidence = spark.sql("""
SELECT table_name, column_name, class_tag
FROM system.data_classification.results
WHERE catalog_name = current_catalog()
  AND schema_name  = current_schema()
  AND class_tag IS NOT NULL
  AND confidence = 'HIGH'
""").collect()

for row in high_confidence:
    stmt = (
        f"ALTER TABLE `{row.table_name}` "
        f"ALTER COLUMN `{row.column_name}` "
        f"SET TAGS ('{row.class_tag}' = '')"
    )
    try:
        spark.sql(stmt)
        print(f"  ✓ {row.table_name}.{row.column_name} → {row.class_tag}")
    except Exception as e:
        print(f"  ✗ {row.table_name}.{row.column_name}: {e}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4 — verify the tags landed

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT table_name, column_name, tag_name
# MAGIC FROM system.information_schema.column_tags
# MAGIC WHERE catalog_name = current_catalog()
# MAGIC   AND schema_name  = current_schema()
# MAGIC   AND tag_name LIKE 'class.%'
# MAGIC ORDER BY table_name, column_name, tag_name;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 5 — build an ABAC policy on system tags
# MAGIC
# MAGIC With system tags applied, you can write ABAC policies using `has_tag(...)` (presence check — system tags don't carry values). One policy can OR together several PII categories.
# MAGIC
# MAGIC > **Heads-up**: this policy will overlap with `mask_all_pii_strings` from notebook 03 (which targets the custom `demo_sensitivity=pii` tag). For the demo, either drop the notebook-03 policy first, OR demonstrate this as an alternative path on a *different* catalog/schema.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Reuses the mask_pii_string UDF from notebook 03 — pure transform, no access control.
# MAGIC -- The policy's TO/EXCEPT clause decides who gets the mask applied (see notebook 03 for rationale).
# MAGIC CREATE OR REPLACE FUNCTION mask_pii_string(column_value STRING)
# MAGIC RETURNS STRING
# MAGIC RETURN '***REDACTED***';

# COMMAND ----------

dbutils.widgets.text("group_pii_readers", "pii-readers", "PII readers group (account-level)")
pii_readers = dbutils.widgets.get("group_pii_readers")

def drop_policy_if_exists(name, target):
    try:
        spark.sql(f"DROP POLICY {name} ON {target}")
    except Exception as e:
        if "POLICY_NOT_FOUND" not in str(e):
            raise

policy_sql = f"""
CREATE POLICY mask_class_pii ON CATALOG `{catalog}`
COMMENT 'Mask any column flagged by UC Data Classification as PII. Applies to all users except pii-readers.'
COLUMN MASK mask_pii_string
TO `account users` EXCEPT `{pii_readers}`
FOR TABLES MATCH COLUMNS
  has_tag('class.email_address')
  OR has_tag('class.us_ssn')
  OR has_tag('class.credit_card')
  OR has_tag('class.phone_number')
  OR has_tag('class.name')
  OR has_tag('class.us_passport')
  AS c
ON COLUMN c
"""
drop_policy_if_exists("mask_class_pii", f"CATALOG `{catalog}`")
spark.sql(policy_sql)
print("Policy mask_class_pii created — targets any column tagged by Data Classification.")

# COMMAND ----------

# MAGIC %sql
# MAGIC -- For non-admin users this is masked; for workspace admins (the demo runner) it's unmasked.
# MAGIC SELECT customer_id, full_name, email, ssn, phone, credit_card FROM customers LIMIT 5;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 6 — see the "create policy from classification" UI flow
# MAGIC
# MAGIC The UI has a one-click "build a policy from this classification" affordance. In Catalog Explorer:
# MAGIC
# MAGIC 1. Open the **Data Classification** panel for the catalog
# MAGIC 2. Click **Review**
# MAGIC 3. Switch to the **User Access** tab
# MAGIC 4. Click **New policy** — the policy form is **pre-filled** with the relevant `has_tag(...)` clauses
# MAGIC 5. Pick a masking function + target principal → save
# MAGIC
# MAGIC This is the demo's strongest moment for the "platform does the work" message — the customer never types a `CREATE POLICY` statement.

# COMMAND ----------

# MAGIC %md
# MAGIC ## When to use which approach
# MAGIC
# MAGIC | If your tag is… | Use |
# MAGIC |-----------------|-----|
# MAGIC | A standard PII category (email, SSN, phone, name, address, DOB, passport, etc.) | **UC Data Classification + system `class.*` tags** (this notebook) |
# MAGIC | An org-specific classification (`executive_only`, `q4_earnings`, `m&a_data`) | **Custom governed tag** (notebook 02 — `demo_sensitivity`) |
# MAGIC | Row-scoping (e.g. region, tenant, business unit) | **Custom governed tag** (notebook 04 — `demo_row_scope`) |
# MAGIC
# MAGIC Most real customers end up with a mix: ~80% of their PII gets covered by system tags, the remaining ~20% needs custom vocabulary.

# COMMAND ----------

# MAGIC %md
# MAGIC ## What's next
# MAGIC
# MAGIC **`03_column_masks_old_vs_new`** continues with the custom-tag path for the mask UDF UDF demo. The story holds with system tags too — just swap `has_tag_value('demo_sensitivity', 'pii')` for `has_tag('class.email_address') OR has_tag('class.us_ssn') OR …`

# COMMAND ----------

# MAGIC %md
# MAGIC ### Teardown (optional)

# COMMAND ----------

# drop_policy_if_exists("mask_class_pii", f"CATALOG `{catalog}`")
# # Unset class.* tags on every column (use the `tagged` query from step 4 to enumerate):
# for row in spark.sql("""
#     SELECT table_name, column_name, tag_name FROM system.information_schema.column_tags
#     WHERE catalog_name = current_catalog() AND schema_name = current_schema()
#       AND tag_name LIKE 'class.%'
# """).collect():
#     spark.sql(f"ALTER TABLE `{row.table_name}` ALTER COLUMN `{row.column_name}` UNSET TAGS ('{row.tag_name}')")
