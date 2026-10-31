"""change_history 판정 컬럼(judgement, judge_reason) 보장 — 멱등·비파괴.

배경: 신규 컬럼은 서버 시작 후 백그라운드 스레드(_run_migrations)에서 추가된다. 그 작업이 끝나기 전이거나
락 대기/오류로 건너뛰어지면, 새 모델이 없는 컬럼을 SELECT 해 500(UndefinedColumn)이 난다.
이 모듈은 요청 처리 전에 필요한 컬럼이 있는지 확인하고, 없을 때만 ADD COLUMN IF NOT EXISTS 를 실행한다.

- 데이터 삭제·수정·테이블 재생성 없음 (컬럼 추가만, 기존 행의 새 컬럼 값은 NULL 유지).
- 이미 컬럼이 있으면 아무것도 실행하지 않는다 (중복 실행 안전).
- PostgreSQL 에서는 lock_timeout 을 짧게 걸어 다른 트랜잭션 때문에 요청이 오래 멈추지 않게 한다.
"""
import logging
import threading
import time

from sqlalchemy import inspect as sa_inspect, text

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = [
    ("change_history", "judgement", "VARCHAR(20)"),
    ("change_history", "judge_reason", "TEXT"),
    # 대리인 이력/차주·대리인 자격정보 분리 (ORM 모델이 SELECT 하므로 요청 전에 보장)
    ("license_holders", "agent_driver_license_number", "VARCHAR(100)"),
    ("license_holders", "agent_freight_qualification_number", "VARCHAR(20)"),
    ("license_holders", "agent_start_date", "VARCHAR(50)"),
    ("license_holders", "owner_driver_license_number", "VARCHAR(100)"),
    ("license_holders", "owner_freight_qualification_number", "VARCHAR(20)"),
    ("transfer_ledger", "agent_name", "VARCHAR(100)"),
    ("transfer_ledger", "agent_resident_number", "VARCHAR(30)"),
    ("transfer_ledger", "agent_mobile", "VARCHAR(50)"),
    ("transfer_ledger", "agent_address", "TEXT"),
    ("transfer_ledger", "agent_driver_license_number", "VARCHAR(100)"),
    ("transfer_ledger", "agent_freight_qualification_number", "VARCHAR(20)"),
    ("transfer_ledger", "agent_start_date", "VARCHAR(50)"),
    ("transfer_ledger", "agent_end_date", "VARCHAR(50)"),
    # 대리인 자격증명발급번호 (컬럼 추가만)
    ("license_holders", "agent_certificate_number", "VARCHAR(100)"),
    ("transfer_ledger", "agent_certificate_number", "VARCHAR(100)"),
    ("member_agents", "certificate_number", "VARCHAR(100)"),
]
_RETRY_SECONDS = 15
_LOCK_WAIT_SECONDS = 20     # 다른 스레드(백그라운드 유지보수)가 보강 중이면 500 대신 잠시 기다린다
_PROBLEM = "필수 스키마"

# ORM 모델이 직접 조회하는 신규 테이블. create_all 한 줄에만 의존하면 앞 단계 실패/락 대기 시 영구 누락되므로
# 이 목록의 테이블은 요청 처리 전에 개별적으로(CREATE TABLE IF NOT EXISTS) 보장한다. 기존 데이터는 건드리지 않는다.
def _required_tables():
    from app import models
    return [models.MemberAgent.__table__]


def missing_tables(engine) -> list:
    insp = sa_inspect(engine)
    return [t.name for t in _required_tables() if not insp.has_table(t.name)]


def ensure_tables(engine) -> bool:
    """필수 테이블이 모두 있으면 True. 없는 테이블만 생성(checkfirst). 데이터 삭제/재생성 없음."""
    global last_error
    try:
        for t in _required_tables():
            if sa_inspect(engine).has_table(t.name):
                continue
            try:
                t.create(bind=engine, checkfirst=True)      # 인덱스 포함, IF NOT EXISTS 의미
                logger.info(f"스키마 보장: 테이블 {t.name} 생성")
            except Exception as e:
                # 다른 스레드/워커가 동시에 만들었을 수 있으니 다시 확인
                if not sa_inspect(engine).has_table(t.name):
                    raise
                logger.info(f"스키마 보장: 테이블 {t.name} 이미 생성됨({type(e).__name__})")
        return not missing_tables(engine)
    except Exception as e:
        last_error = f"{type(e).__name__}: {e}"
        logger.warning(f"테이블 보장 실패(재시도 예정): {last_error}")
        return False

_lock = threading.Lock()
_ready = False
_last_attempt = 0.0
last_error = None


def reset():
    """테스트용: 상태 초기화"""
    global _ready, _last_attempt, last_error
    _ready, _last_attempt, last_error = False, 0.0, None


def is_ready() -> bool:
    return _ready


def ensure_columns(engine) -> bool:
    """필요한 컬럼이 모두 있으면 True. 없으면 추가하고 다시 확인한다. 실패해도 예외를 던지지 않는다."""
    global _ready, last_error
    try:
        if not ensure_tables(engine):      # member_agents 등 신규 테이블 먼저 보장 (없으면 아래 컬럼 확인도 의미 없음)
            return False
        insp = sa_inspect(engine)
        pending = []
        for table, col, ctype in REQUIRED_COLUMNS:
            if not insp.has_table(table):
                return False            # 테이블 자체가 아직 없으면 create_all 이 만든 뒤 다시 확인
            if col not in {c["name"] for c in insp.get_columns(table)}:
                pending.append((table, col, ctype))
        if pending:
            is_sqlite = engine.dialect.name == "sqlite"
            for table, col, ctype in pending:
                with engine.begin() as conn:
                    if not is_sqlite:
                        conn.execute(text("SET LOCAL lock_timeout = '5s'"))
                    clause = "" if is_sqlite else "IF NOT EXISTS "
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {clause}{col} {ctype}"))
                logger.info(f"스키마 보장: {table}.{col} 추가")
            insp = sa_inspect(engine)
            for table, col, _ in pending:
                if col not in {c["name"] for c in insp.get_columns(table)}:
                    return False
        _ready, last_error = True, None
        return True
    except Exception as e:     # 락 대기 초과 등 — 다음 요청에서 재시도
        last_error = f"{type(e).__name__}: {e}"
        logger.warning(f"스키마 보장 실패(재시도 예정): {last_error}")
        return False


def guard_dependency():
    """FastAPI 전역 의존성(동기 함수 → 스레드풀에서 실행). 준비되면 비용이 거의 없다."""
    global _last_attempt
    if _ready:
        return
    now = time.monotonic()
    if now - _last_attempt < _RETRY_SECONDS and _last_attempt:
        return
    if not _lock.acquire(timeout=_LOCK_WAIT_SECONDS):
        return
    try:
        if _ready:
            return
        _last_attempt = now
        from app.database import engine
        ensure_columns(engine)
    finally:
        _lock.release()
