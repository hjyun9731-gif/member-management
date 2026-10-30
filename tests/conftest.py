import os


def pytest_report_header(config):
    url = os.environ.get("DATABASE_URL", "(미설정: 각 테스트가 임시 sqlite 사용)")
    kind = "PostgreSQL" if url.startswith("postgres") else "SQLite"
    return f"테스트 DB 종류: {kind}"
