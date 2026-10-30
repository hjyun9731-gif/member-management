"""One-time, additive DB migration for the agent certificate number.

Run against the SAME database used by Railway, before deploying ORM changes:
    python -m scripts.migrate_agent_certificate_number

Uses DATABASE_URL from environment / app.database and adds columns only when absent.
Never drops/rewrites existing data. Execution requires ALTER TABLE permission.
"""
from sqlalchemy import inspect, text
from app.database import engine

TABLES = ("license_holders", "member_agents", "transfer_ledger")
COLUMN = "agent_certificate_number"

with engine.begin() as conn:
    for table in TABLES:
        if not inspect(conn).has_table(table):
            raise RuntimeError(f"Required table does not exist: {table}")
        if table == "member_agents":
            column = "certificate_number"
        else:
            column = COLUMN
        existing = {c["name"] for c in inspect(conn).get_columns(table)}
        if column not in existing:
            conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" VARCHAR(100)'))
            print(f"added: {table}.{column}")
        else:
            print(f"already exists: {table}.{column}")
print("Agent certificate number DB migration complete.")
