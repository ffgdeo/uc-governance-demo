# Unity Catalog Governance Demo

A reusable demo showing how **Governed Tags + ABAC policies + column masks + row filters** in Unity Catalog let you classify data once and enforce permissions everywhere.

Audience: customers asking "how do I manage permissions across hundreds of tables without writing one policy per column?"

> **Tested 2026-05-27** on a serverless FEVM workspace (`fevm-fd-serverless-workspace`) against `fd_serverless_workspace_catalog`. Every notebook's SQL ran end-to-end; the governance-loop story (data gen → classify → tag → mask + filter → ai_mask → lineage → audit) was validated.

---

## What this demo shows

| # | Notebook | Talking point |
|---|----------|--------------|
| 00 | `setup` | Create catalog/schema, set up demo personas |
| 01 | `generate_data` | Faker-driven synthetic customers + transactions + free-text support notes with embedded PII |
| 02 | `discover_classify_tag` | **`ai_classify`** auto-detects PII columns; **Governed Tags** classify data with controlled vocabulary |
| 03 | `column_masks_old_vs_new` | Legacy per-column `SET MASK` (one policy per column per table) **vs** ABAC `CREATE POLICY ... COLUMN MASK` (one policy, all PII columns everywhere) |
| 04 | `row_filters_abac` | Row-level filtering driven by tag + group membership |
| 05 | `ai_mask_unstructured` | **`ai_mask`** redacts PII inside free-text columns (where structured masking can't help) |
| 06 | `lineage_and_propagation` | Tags + policies follow data through lineage to downstream views/tables |
| 07 | `audit_system_tables` | `system.access.audit` + `system.information_schema` for the "governance loop" story |

---

## Prerequisites

- **Unity Catalog enabled** workspace (any cloud)
- **DBR 16.4+** or a **SQL warehouse on the latest channel** (governed tags need DBR 16.1+; ABAC policies are in Public Preview and need 16.4+)
- **Account/workspace admin** access to create governed tags and ABAC policies
- **`ai_classify` / `ai_mask`** require Foundation Model APIs enabled in your workspace (Pay-per-token endpoints are fine)
- **Three groups** in your workspace (or pass your own group names as widgets):
  - `data-stewards` — can apply governed tags
  - `pii-readers` — can see PII unmasked
  - `analysts-east` / `analysts-west` — restricted by row filter to their region

If the groups don't exist yet, an account admin can create them in the Account Console → User management. The notebooks log a warning if they're missing but won't fail.

---

## Demo personas

| Persona | Member of | Sees |
|---------|-----------|------|
| You (admin) | `account admins` | Everything unmasked, all regions |
| HR / compliance | `pii-readers` | Everything unmasked, all regions |
| East-coast analyst | `analysts-east` | PII masked, only `region = 'east'` rows |
| West-coast analyst | `analysts-west` | PII masked, only `region = 'west'` rows |

The fastest way to "switch personas" in a live demo is to temporarily remove yourself from `account admins` and add yourself to one of the analyst groups, then re-query. Have a second browser/incognito window logged in as a non-admin teammate if available.

---

## Run order

```
notebooks/00_setup.py
notebooks/01_generate_data.py
notebooks/02_discover_classify_tag.py
notebooks/03_column_masks_old_vs_new.py
notebooks/04_row_filters_abac.py
notebooks/05_ai_mask_unstructured.py
notebooks/06_lineage_and_propagation.py
notebooks/07_audit_system_tables.py
```

Every notebook is parameterized via widgets — set `catalog` and `schema` once at the top of each. Defaults: `catalog=demos`, `schema=uc_governance`.

---

## How to run this in a customer demo

1. **Clone into a Databricks workspace.** Add Repo → `https://github.com/ffgdeo/uc-governance-demo.git` (or your fork).
2. **Run 00 + 01 ahead of the call.** Data generation takes 1–2 min and isn't a great live demo. Have the data ready.
3. **Live-demo 02 through 07** in order. Each notebook is ~5–10 min of screen-share.
4. **Trim based on customer interest.** Notebook 02 is the foundation. 03 and 04 are the "money slides." 05/06/07 are bonus depending on time.

### Tear-down

A `_teardown.sql` cell at the bottom of each notebook removes what it created. Or just `DROP CATALOG demos CASCADE` (after detaching policies) when you're done.

---

## Gotchas (learned the hard way while building this)

- **Governed tags live at the ACCOUNT level.** Pick demo-unique names like `demo_sensitivity` — a generic `sensitivity_level` will collide with whatever your customer or another demo already created. `CREATE GOVERNED TAG` fails outright on collision.
- **`column_value`** is the required parameter name inside `COLUMN MASK` UDFs — use that exact identifier, don't rename it.
- **The two policy clause shapes are different:**
  - `COLUMN MASK fn TO grp FOR TABLES MATCH COLUMNS … AS c ON COLUMN c`
  - `ROW FILTER fn TO grp FOR TABLES MATCH COLUMNS … AS c USING COLUMNS (c)`
  - `ROW FILTER` uses `USING COLUMNS (…)`, not `ON COLUMN`. Easy to mix up.
- **No overlapping ABAC matches.** UC rejects queries where two policies match the same column. Mixing a STRING mask and a DATE mask on the same `pii` tag won't work — use per-type tag values (`pii_string`, `pii_date`) and one policy per type. Notebook 03 explains the pattern.
- **Add the demo-runner bypass.** UDFs in notebooks 03–05 include `is_member('admins')` so workspace admins can sanity-check unmasked output. Remove that clause for production rollouts.
- **`MATCH COLUMNS` predicates are limited.** Only `has_tag()` and `has_tag_value()` are supported — no type filtering or other expressions.
- **`SET TAG … ON COLUMN` doesn't parse.** Despite appearing in some older docs, the working syntax is `ALTER TABLE … ALTER COLUMN … SET TAGS ('tag' = 'value')`.
- **`DROP GOVERNED TAG IF EXISTS` is not supported.** Drop without `IF EXISTS`, or use try/except.
- **Re-running notebook 05** drops and re-creates `demo_sensitivity` to add a new value (`pii_freetext`). Requires you to be the tag creator OR an account admin.
- **Tag inheritance** flows catalog → schema → table → column. A tag set at catalog level is visible (but not editable) on every child.
- **ABAC is Public Preview** as of this writing. Verify against your workspace's release channel before promising customers GA dates.
- **System tables lag** by ~15 min for audit, near-real-time for `information_schema`.

---

## File map

```
uc-governance-demo/
├── README.md                              # this file
├── .gitignore
└── notebooks/
    ├── 00_setup.py
    ├── 01_generate_data.py
    ├── 02_discover_classify_tag.py
    ├── 03_column_masks_old_vs_new.py
    ├── 04_row_filters_abac.py
    ├── 05_ai_mask_unstructured.py
    ├── 06_lineage_and_propagation.py
    └── 07_audit_system_tables.py
```
