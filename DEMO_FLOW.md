# Demo Flow — UC Governance Walkthrough

A ~30 min customer-facing demo of Unity Catalog governed tags, data classification, ABAC policies, masking, row filters, and audit. Designed to answer the question *"how do I manage permissions across hundreds of tables without writing one policy per column?"*

The arc: **discover → classify → enforce → prove**.

---

## Before the call

| What | Why |
|------|-----|
| Run `00_setup` + `01_generate_data` once | Data gen isn't compelling live — have it ready |
| Enable Data Classification on the target catalog/schema | Scan needs ~30 min; ideally run hours/days before so suggestions are queued up |
| Confirm policies + tags are in place (see "Quick health check" below) | A demo where the policies are missing is awkward |
| Open these tabs ahead of time: Catalog Explorer → `customers` (Sample Data tab); Data Classification panel; Policies tab; one SQL editor tab | Faster transitions during the call |
| Optional but powerful: second browser logged in as a non-admin teammate | Persona switch lands much harder |

### Quick health check

Run in a SQL editor against `fd_serverless_workspace_catalog.uc_governance_demo`:

```sql
SELECT 'tags' AS what, count(*) AS n FROM system.information_schema.column_tags
WHERE catalog_name = 'fd_serverless_workspace_catalog' AND schema_name = 'uc_governance_demo'
UNION ALL SELECT 'classification_results', count(*) FROM system.data_classification.results
WHERE catalog_name = 'fd_serverless_workspace_catalog' AND schema_name = 'uc_governance_demo' AND class_tag IS NOT NULL
UNION ALL SELECT 'policies', count(*) FROM (SELECT 1 FROM system.information_schema.tables
WHERE table_catalog = 'fd_serverless_workspace_catalog' LIMIT 1);  -- there's no info_schema.policies; use SHOW POLICIES ON CATALOG fd_serverless_workspace_catalog
```

Expected: 15+ tags, 15+ classification results, 3 policies. If classification_results is 0, the scan hasn't run on this schema yet.

---

## The flow

### 1. The problem (2 min) — Catalog Explorer
Open `fd_serverless_workspace_catalog.uc_governance_demo.customers` → **Sample Data** tab.

> *"Email. SSN. Credit card. DOB. Name. Address. Every column an analyst could see today. Multiply that by a few hundred tables in production, and the question becomes: how do you scale governance?"*

Click through a few columns. Mention the count of rows. Make the data feel real.

---

### 2. The platform discovers it for you (4 min) — Data Classification UI
Catalog → **Details** tab → **Data Classification** panel. (Or the direct URL: `/governance/data-classification?catalog=fd_serverless_workspace_catalog`.)

> *"Before you build anything, Databricks already scanned this catalog. Look at what it found."*

Walk through the findings:
- `customers.ssn` → `class.us_ssn`, HIGH, frequency 1.0
- `customers.email` → `class.email_address`, HIGH, frequency 1.0
- `customers.credit_card`, `.phone`, `.full_name`, `.dob`, `.address` → all flagged correctly

**The money moment**: open `support_notes.note_text`. It's tagged with **multiple** categories — `class.email_address`, `class.phone_number`, `class.credit_card`, `class.name`, `class.location` — *all on one column*. Show sample values.

> *"This column is just a free-text 'notes' field. The classifier didn't just match the column name — it read inside the values and found every PII type embedded. This is purpose-built for PII."*

---

### 3. Apply the tags (1 min) — UI
Click **Review** in the Data Classification panel. Show the per-column suggestion list with confidence + sample values.

> *"This is the data-steward workflow. Accept, reject, or leave for later. Each accept turns into a real column tag."*

Click **Accept** on a few rows, or **Accept All**. Pop over to `customers.email` → tags now visible in the column detail.

> Skip this step if you've already accepted them; the tags are already applied.

---

### 4. Build a policy directly from classification (3 min) — UI's killer feature
Back to Data Classification → **Review** → **User Access** tab → **New policy**.

The policy form is **pre-filled** with `has_tag('class.email_address') OR has_tag('class.us_ssn') OR ...` — the OR list comes from whatever class.* tags were applied.

> *"You never type a CREATE POLICY statement. Pick the mask function, pick who it applies to, save."*

Pick `mask_pii_string` (already created in this workspace) → `account users` → save.

---

### 5. Prove it works (2 min) — SQL editor
Open a SQL tab.

```sql
SELECT customer_id, full_name, email, ssn, phone, credit_card
FROM fd_serverless_workspace_catalog.uc_governance_demo.customers LIMIT 5;
```

As workspace admin → unmasked (the UDF has an admin bypass). Now switch personas — one of:

- **Incognito browser** logged in as a non-admin teammate → re-run → `***REDACTED***`
- **Temporarily remove yourself from `admins`** → re-run → `***REDACTED***` → re-add yourself
- **Narrate**: *"Watch what happens when a non-admin runs this — same SQL, redacted output. The platform decides what each user sees."* (least convincing, but works in a pinch)

---

### 6. "But what about *my* classifications?" (2 min) — notebook 02
Pivot to the custom-tag story.

> *"System tags cover ~80% of what most customers need — standard PII categories. For the 20% that's organization-specific — 'executive_only', 'q4_earnings', 'pre-IPO data' — you write a custom governed tag. Same plumbing, your vocabulary."*

Open notebook `02_discover_classify_tag.py`. Show the `CREATE GOVERNED TAG demo_sensitivity VALUES ('pii', 'financial', 'public')` cell. Don't run it (already exists) — just show that custom tags have the same structure as system tags.

If there's time, show the `ai_classify` SQL pattern — same idea as the managed scanner, useful when you want programmatic control.

---

### 7. Row filters (3 min) — notebook 04
Pivot again:

> *"Column masks hide *values*. Row filters hide *rows*."*

Open notebook 04. The `region_row_filter` policy is already in place. Run:

```sql
SELECT region, count(*) FROM fd_serverless_workspace_catalog.uc_governance_demo.customers GROUP BY region;
SELECT region, count(*) FROM fd_serverless_workspace_catalog.uc_governance_demo.transactions GROUP BY region;
```

As admin → see all regions. *"Two tables, same filter, one ABAC policy. Tag a new table's region column and it's filtered automatically."*

If you have a second persona, switch and show that an `analysts-east` user only sees `region = 'east'` rows.

---

### 8. Unstructured PII (3 min) — notebook 05
> *"Column masks work great on structured columns. But what if PII is embedded inside a free-text field?"*

Pull up a `support_notes.note_text` row in Catalog Explorer (or a SQL query) — show the raw text with email/phone/SSN/credit card inline.

Run:
```sql
SELECT ai_mask(note_text, ARRAY('email','phone','ssn','credit card number','street address','person name'))
FROM fd_serverless_workspace_catalog.uc_governance_demo.support_notes LIMIT 3;
```

PII tokens get replaced inline. *"And the classifier already flagged this column for us — we didn't have to teach the platform what was in there."*

Then show that this is wired into the `mask_pii_freetext_columns` ABAC policy.

---

### 9. Lineage (2 min) — UI
Catalog Explorer → `customer_transaction_summary` (the view) → **Lineage** tab. Show the upstream graph: view → customers + transactions.

> *"Build a view, build a mart — policies follow the data. An analyst can't bypass the masking by building a downstream object."*

Mention the gotcha: CTAS into a *new* table strips the upstream tags — re-tag the mart explicitly.

---

### 10. Prove it — governance loop (3 min) — UI + SQL
Catalog Explorer → catalog → **Policies** tab. Show all four/five policies in force across the catalog.

Then open notebook 07 and run:
- Tag-coverage % (`% of columns classified`)
- Recent tag changes in `system.access.audit`
- Recent policy creations

> *"This is your governance dashboard. SOX, GDPR, HIPAA — whatever framework, the evidence is queryable. Tag → policy → audit, all in one platform."*

---

## Closing slide

| | Old way | UC governance |
|---|---|---|
| Discover PII | Custom regex + ai_classify jobs | **Built-in classifier scans automatically** |
| Vocabulary | Define your own | **`class.*` system tags pre-built** |
| Apply tags | Per-column DDL | **One-click in Review UI** |
| Build policy | `CREATE POLICY ...` per column | **Pre-filled from classification UI** |
| Enforce | Per-table masks/filters | **One ABAC policy → all tagged columns** |
| Audit | Custom logging | **`system.access.audit` + `information_schema`** |

---

## If you have less time

- **15 min cut**: steps 1, 2, 4 (policy from classification UI), 5 (prove it), 10 (governance loop).
- **45 min cut**: full flow + walk through the audit notebook in more depth, optionally show the SDLC story (committing policy DDL to git for review/PR/approval flow).

## Common questions

- **"How is this different from regex / column-name matching?"** — Classifier reads values, handles unstructured text, multi-language. Show the support_notes row.
- **"What's the GA timeline for ABAC?"** — Public Preview as of 2026-05. Verify against current docs before committing.
- **"What happens when a new column is added?"** — Demo this live: ADD COLUMN, tag it, mask kicks in instantly (notebook 03's passport_number step).
- **"Can the steward override the classifier?"** — Yes, the Review UI lets you reject/edit each suggestion.
- **"What's the cost?"** — Classification runs on serverless background compute, billed via `system.billing.usage` with `billing_origin_product = 'DATA_CLASSIFICATION'`.
- **"How long does the scan take?"** — Small catalogs: ~30 min. Large ones: incremental, finishes within ~24 h. New tables get scanned within ~24 h of creation.
