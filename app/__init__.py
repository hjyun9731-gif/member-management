"""Application package bootstrap.

Additive schema guard for the Closure management-number split.
It only creates missing columns; it never updates/deletes existing rows.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _ensure_closure_management_columns() -> None:
    database_url = (os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        return
    try:
        from sqlalchemy import create_engine, inspect, text

        if database_url.startswith("postgres://"):
            database_url = "postgresql://" + database_url[len("postgres://"):]

        engine = create_engine(database_url, pool_pre_ping=True)
        try:
            inspector = inspect(engine)
            if "closures" not in inspector.get_table_names():
                return
            existing = {c["name"] for c in inspector.get_columns("closures")}
            required = (
                ("original_management_number", "VARCHAR(100)"),
                ("original_mgmt_match_status", "VARCHAR(50)"),
            )
            with engine.begin() as conn:
                for column_name, column_type in required:
                    if column_name in existing:
                        continue
                    if database_url.startswith("sqlite"):
                        conn.execute(text(f"ALTER TABLE closures ADD COLUMN {column_name} {column_type}"))
                    else:
                        conn.execute(text(f"ALTER TABLE closures ADD COLUMN IF NOT EXISTS {column_name} {column_type}"))
                    logger.warning("[closure-schema-guard] added closures.%s", column_name)
            logger.info("[closure-schema-guard] management-number columns ready")
        finally:
            engine.dispose()
    except Exception as exc:
        logger.exception("[closure-schema-guard] failed: %s", exc)


_ensure_closure_management_columns()
