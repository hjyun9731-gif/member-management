import os
import tempfile

# 모든 DB 쓰기/DDL 테스트는 공유 운영 DB나 과거 실행 파일을 절대 건드리지 않도록
# pytest 프로세스마다 별도의 SQLite DB를 사용한다. 모듈별 setdefault보다 먼저 실행.
# TemporaryDirectory 참조를 프로세스 종료까지 유지해서 DB 파일 수명을 보장한다.
_TEST_DB_DIR = tempfile.TemporaryDirectory(prefix="mm_test_suite_", ignore_cleanup_errors=True)
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_DIR.name}/suite.sqlite"


def pytest_report_header(config):
    url = os.environ.get("DATABASE_URL", "(미설정: 각 테스트가 임시 sqlite 사용)")
    kind = "PostgreSQL" if url.startswith("postgres") else "SQLite"
    return f"테스트 DB 종류: {kind}"


def pytest_sessionfinish(session, exitstatus):
    """Release the shared SQLite engine before Windows removes the test DB."""
    import gc
    import sys
    database_module = sys.modules.get("app.database")
    if database_module is not None:
        database_module.engine.dispose()
    gc.collect()
