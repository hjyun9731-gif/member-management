"""Early additive schema guard for the closures management-number fields.

Why this exists:
- Current ORM model expects closures.original_management_number and
  closures.original_mgmt_match_status.
- Production PostgreSQL may still be on the older schema, causing every
  Closure ORM SELECT to fail with psycopg2.errors.UndefinedColumn.

Safety:
- PostgreSQL only.
- ADD COLUMN IF NOT EXISTS only.
- No DROP / DELETE / UPDATE / TRUNCATE.
- Does not alter member, receivables, closure business logic.
- Runs before app.main / app.models / app.routers are imported because
  Python imports package app.__init__ first.
"""
from __future__ import annotations

import os
import sys


def _log(message: str) -> None:
    try:
        print(f"[closure-schema-guard] {message}", file=sys.stderr, flush=True)
    except Exception:
        pass


def _ensure_closure_management_columns() -> None:
    database_url = (os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        return
    if database_url.startswith("postgres://"):
        database_url = "postgresql://" + database_url[len("postgres://"):]
    if not database_url.startswith("postgresql"):
        return

    engine = None
    try:
        from sqlalchemy import create_engine, text

        # Separate one-shot connection so this runs before the application's
        # normal SQLAlchemy model queries. Small pool; immediately disposed.
        engine = create_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=1,
            max_overflow=0,
        )
        with engine.begin() as conn:
            # Only touch closures if the table already exists. Normal app
            # create_all/migrations remain responsible for first-time setup.
            conn.execute(text("""
                DO $$
                BEGIN
                    IF to_regclass('public.closures') IS NOT NULL THEN
                        ALTER TABLE closures
                            ADD COLUMN IF NOT EXISTS original_management_number VARCHAR(100);
                        ALTER TABLE closures
                            ADD COLUMN IF NOT EXISTS original_mgmt_match_status VARCHAR(50);
                    END IF;
                END $$;
            """))
        _log("closures management-number columns OK")
    except Exception as exc:
        # Keep the exact problem visible in Railway logs; do not mutate any
        # other schema or business data.
        _log(f"WARNING {type(exc).__name__}: {exc}")
    finally:
        try:
            if engine is not None:
                engine.dispose()
        except Exception:
            pass


_ensure_closure_management_columns()
