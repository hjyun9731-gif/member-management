"""Add nullable agent certificate number columns (idempotent, PostgreSQL/SQLite).

Run against the existing application database before deploying the code change:
    python scripts/migrate_agent_certificate_number.py

The DATABASE_URL used by app.database must point to the intended database.
"""
from sqlalchemy import inspect, text
from app.database import engine

COLUMNS = {
    "license_holders": "agent_certificate_number",
    "member_agents": "certificate_number",
    "transfer_ledger": "agent_certificate_number",
}

with engine.begin() as connection:
    for table, column in COLUMNS.items():
        if not inspect(connection).has_table(table):
            raise RuntimeError(f"Missing database table: {table}")
        existing = {c["name"] for c in inspect(connection).get_columns(table)}
        if column not in existing:
            connection.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" VARCHAR(100)'))
            print(f"Added {table}.{column}")
        else:
            print(f"Already exists: {table}.{column}")
