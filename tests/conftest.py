import os
import pytest


@pytest.fixture(autouse=True)
def isolate_background_startup(monkeypatch):
    """fixture가 테이블을 재생성하는 동안 별도 startup 스레드가 같은 DB를 변경하지 않게 한다.
    스키마 보장/마이그레이션은 해당 테스트가 직접 실행하여 검증한다.
    운영 startup 코드는 변경하지 않는다.
    """
    import app.main as main
    monkeypatch.setattr(main, "_run_deferred_db_maintenance", lambda: None)
    monkeypatch.setattr(main, "create_default_admin", lambda db: None)


def pytest_report_header(config):
    url = os.environ.get("DATABASE_URL", "(미설정: 각 테스트가 임시 sqlite 사용)")
    kind = "PostgreSQL" if url.startswith("postgres") else "SQLite"
    return f"테스트 DB 종류: {kind}"
