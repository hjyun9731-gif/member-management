import os
import tempfile
import pytest
from db_support import postgres_url, cleanup_engines

# 모든 DB 쓰기/DDL 테스트는 공유 운영 DB나 과거 실행 파일을 절대 건드리지 않도록
# 기본은 매 실행마다 임시 SQLite. PostgreSQL은 검증된 loopback mm_test_* DB만 허용한다.
# TemporaryDirectory 참조를 프로세스 종료까지 유지해서 DB 파일 수명을 보장한다.
_TEST_DB_DIR = tempfile.TemporaryDirectory(prefix="mm_test_suite_", ignore_cleanup_errors=True)
_PG_URL = postgres_url()
os.environ["DATABASE_URL"] = (_PG_URL.render_as_string(hide_password=False) if _PG_URL else
                              f"sqlite:///{_TEST_DB_DIR.name}/suite.sqlite")


def pytest_report_header(config):
    url = os.environ["DATABASE_URL"]
    kind = "PostgreSQL" if url.startswith("postgres") else "SQLite"
    return f"테스트 DB 종류: {kind}"


def pytest_sessionfinish(session, exitstatus):
    """Release the shared SQLite engine before Windows removes the test DB."""
    import gc
    import sys
    database_module = sys.modules.get("app.database")
    if database_module is not None:
        database_module.engine.dispose()
    cleanup_engines()
    gc.collect()


@pytest.fixture(autouse=True)
def release_test_sessions():
    """Legacy tests open ad-hoc sessions; release read locks before the next DDL fixture."""
    from sqlalchemy.orm import close_all_sessions
    yield
    close_all_sessions()
