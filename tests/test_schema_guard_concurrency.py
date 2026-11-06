"""Regression: simultaneous SQLite schema checks must not race on ADD COLUMN."""
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import create_engine, inspect, text

from app.services import schema_guard


def test_sqlite_columns_are_added_once_under_concurrency(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'schema_race.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 20},
    )
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE change_history (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(text("INSERT INTO change_history (id, name) VALUES (1, 'preserve-me')"))

    monkeypatch.setattr(schema_guard, "ensure_tables", lambda e: True)
    monkeypatch.setattr(schema_guard, "REQUIRED_COLUMNS", [
        ("change_history", "judgement", "VARCHAR(20)"),
        ("change_history", "judge_reason", "TEXT"),
    ])
    schema_guard.reset()
    try:
        with ThreadPoolExecutor(max_workers=8) as workers:
            results = list(workers.map(schema_guard.ensure_columns, [engine] * 16))
        assert all(results), schema_guard.last_error
        columns = {c["name"] for c in inspect(engine).get_columns("change_history")}
        assert {"id", "name", "judgement", "judge_reason"} <= columns
        with engine.connect() as conn:
            assert conn.execute(text("SELECT name FROM change_history WHERE id=1")).scalar() == "preserve-me"
        assert schema_guard.ensure_columns(engine) is True  # repeated calls also safe
    finally:
        engine.dispose()
        schema_guard.reset()
