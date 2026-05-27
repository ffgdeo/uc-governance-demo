# Unity Catalog Governance Demo

A reusable demo showing how **Governed Tags + ABAC policies + column masks + row filters** in Unity Catalog let you classify data once and enforce permissions everywhere.

Audience: customers asking "how do I manage permissions across hundreds of tables without writing one policy per column?"

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

## Gotchas

- **`column_value`** is the implicit parameter name inside `COLUMN MASK` UDFs — use that exact identifier, don't rename it (one of the more common ABAC bugs).
- **Tag inheritance** flows catalog → schema → table → column. A tag set at catalog level is visible (but not editable) on every child. Resolve conflicts by setting the more specific level.
- **Policies are evaluated by privilege, not by user.** `TO 'pii-readers'` actually means "users who *are members of* `pii-readers`." Same group can be granted via multiple policies — they OR together.
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
