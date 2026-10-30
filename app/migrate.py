"""배포 시작 전 DB 마이그레이션 실행·검증 (python -m app.migrate).

- 멱등·비파괴: 없는 테이블/컬럼만 추가한다. 기존 데이터 삭제·초기화·재생성 없음.
- 검증 실패 시 종료코드 1 → 배포 시작 명령(&&)이 중단되어 Railway 가 이전 정상 배포를 유지한다.
- 접속 문자열 등 비밀값은 출력하지 않는다.
"""
import logging
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("migrate")


def run(max_wait: int = 90, interval: int = 5) -> int:
    from sqlalchemy import inspect, text
    from app.database import engine
    from app.services import schema_guard
    import app.models  # noqa: F401  (모델 등록)

    deadline = time.monotonic() + max_wait
    attempt = 0
    while True:
        attempt += 1
        try:
            with engine.connect() as c:
                c.execute(text("SELECT 1"))
            try:    # 신규 DB 대비(기존 테이블은 checkfirst 로 건드리지 않음). 실패해도 아래 개별 보장으로 이어간다.
                from app.database import Base
                Base.metadata.create_all(bind=engine, checkfirst=True)
            except Exception as e:
                log.warning("create_all 경고(무시, 개별 보장 계속): %s", type(e).__name__)
            ok = schema_guard.ensure_columns(engine)       # 필수 테이블 → 필수 컬럼 순으로 보장
            miss_t = schema_guard.missing_tables(engine)
            insp = inspect(engine)
            miss_c = [f"{t}.{col}" for t, col, _ in schema_guard.REQUIRED_COLUMNS
                      if insp.has_table(t) and col not in {x["name"] for x in insp.get_columns(t)}]
            if ok and not miss_t and not miss_c:
                log.info("마이그레이션 검증 통과: 필수 테이블/컬럼 모두 존재 (시도 %d)", attempt)
                return 0
            log.warning("검증 미통과(시도 %d): 누락 테이블=%s 누락 컬럼=%s last_error=%s",
                        attempt, miss_t, miss_c, schema_guard.last_error)
        except Exception as e:                              # DB 일시 불가 등
            log.warning("DB 연결/마이그레이션 오류(시도 %d): %s", attempt, type(e).__name__)
        if time.monotonic() + interval > deadline:
            log.error("마이그레이션 검증 실패 — 배포를 중단합니다.")
            return 1
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(run())
