# Databricks notebook source
# MAGIC %md
# MAGIC # 01 — Generate Synthetic PII Data
# MAGIC
# MAGIC Generates three tables of realistic synthetic PII data using Faker:
# MAGIC
# MAGIC | Table | What's in it |
# MAGIC |-------|--------------|
# MAGIC | `customers` | Structured PII: email, ssn, credit_card, phone, dob, address, region |
# MAGIC | `transactions` | Linked to customers: amount, merchant_category, txn_date, region |
# MAGIC | `support_notes` | **Unstructured** free-text containing embedded PII — for the `ai_mask` demo |
# MAGIC
# MAGIC All widgets default to small row counts so you can iterate quickly. Bump them up for a more impressive demo.

# COMMAND ----------

# MAGIC %pip install faker --quiet
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

dbutils.widgets.text("catalog", "demos", "Catalog")
dbutils.widgets.text("schema", "uc_governance", "Schema")
dbutils.widgets.text("n_customers", "5000", "Number of customers")
dbutils.widgets.text("n_transactions", "50000", "Number of transactions")
dbutils.widgets.text("n_support_notes", "1000", "Number of support notes")
dbutils.widgets.text("seed", "42", "Random seed")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
n_customers = int(dbutils.widgets.get("n_customers"))
n_transactions = int(dbutils.widgets.get("n_transactions"))
n_support_notes = int(dbutils.widgets.get("n_support_notes"))
seed = int(dbutils.widgets.get("seed"))

spark.sql(f"USE CATALOG `{catalog}`")
spark.sql(f"USE SCHEMA `{schema}`")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Generate `customers`

# COMMAND ----------

from faker import Faker
from pyspark.sql import Row
import random

fake = Faker()
Faker.seed(seed)
random.seed(seed)

REGIONS = ["east", "west", "central"]

def make_customer(i):
    return Row(
        customer_id=i,
        full_name=fake.name(),
        email=fake.email(),
        ssn=fake.ssn(),
        credit_card=fake.credit_card_number(),
        phone=fake.phone_number(),
        dob=fake.date_of_birth(minimum_age=18, maximum_age=85),
        address=fake.street_address(),
        city=fake.city(),
        state=fake.state_abbr(),
        zip_code=fake.postcode(),
        region=random.choice(REGIONS),
        signup_date=fake.date_between(start_date="-3y", end_date="today"),
    )

customers_rows = [make_customer(i) for i in range(1, n_customers + 1)]
customers_df = spark.createDataFrame(customers_rows)
(
    customers_df.write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("customers")
)
print(f"customers: {customers_df.count():,} rows")
display(spark.table("customers").limit(5))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Generate `transactions`

# COMMAND ----------

MERCHANT_CATEGORIES = [
    "Groceries", "Gas", "Restaurants", "Online Shopping",
    "Travel", "Entertainment", "Healthcare", "Utilities",
]

# Pre-fetch (customer_id, region) so transactions inherit the customer's region
cust_pairs = [
    (r.customer_id, r.region)
    for r in spark.table("customers").select("customer_id", "region").collect()
]

def make_transaction(i):
    cust_id, region = random.choice(cust_pairs)
    return Row(
        transaction_id=i,
        customer_id=cust_id,
        region=region,
        amount=round(random.uniform(2.5, 2500.0), 2),
        merchant_category=random.choice(MERCHANT_CATEGORIES),
        merchant_name=fake.company(),
        txn_date=fake.date_between(start_date="-1y", end_date="today"),
    )

txn_rows = [make_transaction(i) for i in range(1, n_transactions + 1)]
txn_df = spark.createDataFrame(txn_rows)
(
    txn_df.write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("transactions")
)
print(f"transactions: {txn_df.count():,} rows")
display(spark.table("transactions").limit(5))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Generate `support_notes` (free-text with embedded PII)
# MAGIC
# MAGIC This is the table we'll demo `ai_mask` on. Each note is a short paragraph that may mention the customer's email, phone, SSN, or credit card inline — the kind of unstructured text that structured column masks can't help with.

# COMMAND ----------

NOTE_TEMPLATES = [
    "Customer called about a billing issue. Reachable at {phone} or {email}. Verified identity with last 4 of SSN {ssn_last4}.",
    "Follow-up needed. Customer's credit card {credit_card} was declined twice. Asked them to confirm via {email}.",
    "Card replacement requested. Old card {credit_card}. Shipping to {address}. Confirmed phone {phone}.",
    "Identity verification call. SSN on file ends in {ssn_last4}. Email of record {email}.",
    "Dispute filed for transaction. Customer reachable at {phone}. Sent confirmation to {email}.",
    "Account access locked. Reset link emailed to {email}. Backup phone {phone}.",
]

def make_note(i):
    c = random.choice(cust_pairs)
    cust_id = c[0]
    full = spark.table("customers").filter(f"customer_id = {cust_id}").collect()[0] if False else None
    # avoid per-row collect — synthesize plausible inline PII directly
    return Row(
        note_id=i,
        customer_id=cust_id,
        note_date=fake.date_between(start_date="-1y", end_date="today"),
        agent_name=fake.name(),
        note_text=random.choice(NOTE_TEMPLATES).format(
            phone=fake.phone_number(),
            email=fake.email(),
            ssn_last4=str(random.randint(1000, 9999)),
            credit_card=fake.credit_card_number(),
            address=fake.street_address(),
        ),
    )

note_rows = [make_note(i) for i in range(1, n_support_notes + 1)]
notes_df = spark.createDataFrame(note_rows)
(
    notes_df.write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable("support_notes")
)
print(f"support_notes: {notes_df.count():,} rows")
display(spark.table("support_notes").limit(5))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Sanity-check counts and a sample join

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT
# MAGIC   (SELECT count(*) FROM customers)     AS customers,
# MAGIC   (SELECT count(*) FROM transactions)  AS transactions,
# MAGIC   (SELECT count(*) FROM support_notes) AS support_notes;

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT c.region, count(*) AS txn_count, round(sum(t.amount), 2) AS total_amount
# MAGIC FROM customers c
# MAGIC JOIN transactions t USING (customer_id)
# MAGIC GROUP BY c.region
# MAGIC ORDER BY total_amount DESC;

# COMMAND ----------

# MAGIC %md
# MAGIC ## What's next
# MAGIC
# MAGIC Continue to **`02_discover_classify_tag`** to auto-detect PII columns with `ai_classify` and apply governed tags.

# COMMAND ----------

# MAGIC %md
# MAGIC ### Teardown (optional)

# COMMAND ----------

# spark.sql("DROP TABLE IF EXISTS customers")
# spark.sql("DROP TABLE IF EXISTS transactions")
# spark.sql("DROP TABLE IF EXISTS support_notes")
