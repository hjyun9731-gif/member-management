from dotenv import load_dotenv
load_dotenv()  # .env 파일 로딩 (Railway에서는 환경변수가 자동 주입됨)

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from app.database import Base, engine, SessionLocal, DATABASE_URL
from app.auth import create_default_admin
from app.routers import (auth, dashboard, reports, excel)
from app.routers import (candidates, members, transfer_ledger,
                          closures, change_history, allocation, admin)
import app.models as _models

# 테이블 생성 (없는 경우만 - checkfirst=True로 기존 테이블 충돌 방지)
try:
    _models.Base.metadata.create_all(bind=engine, checkfirst=True)
    db_type = "SQLite" if "sqlite" in DATABASE_URL else "PostgreSQL"
    logger.info(f"DB 연결 완료 ({db_type})")
except Exception as e:
    # create_all 실패해도 서버는 계속 기동 (테이블이 이미 존재하는 경우)
    logger.warning(f"DB create_all 경고 (무시, 테이블 이미 존재): {e}")

# 컬럼 마이그레이션: 새 컬럼이 없으면 추가
def _run_migrations():
    """신규 컬럼이 기존 DB에 없을 경우 ALTER TABLE로 추가 (컬럼별 독립 트랜잭션)"""
    from sqlalchemy import text, inspect as sa_inspect
    is_sqlite = "sqlite" in DATABASE_URL

    # 현재 license_holders 테이블의 실제 컬럼 목록 조회
    try:
        inspector = sa_inspect(engine)
        existing_cols = {c["name"] for c in inspector.get_columns("license_holders")}
        logger.info(f"현재 license_holders 컬럼: {sorted(existing_cols)}")
    except Exception as ex:
        logger.warning(f"컬럼 조회 실패 (전체 마이그레이션 시도): {ex}")
        existing_cols = set()

    new_cols = [
        ("reapproval_date",       "license_holders",  "VARCHAR(50)"),
        ("official_address",      "license_holders",  "TEXT"),
        ("agent_name",            "license_holders",  "VARCHAR(100)"),
        ("agent_resident_number", "license_holders",  "VARCHAR(30)"),
        ("agent_mobile",          "license_holders",  "VARCHAR(50)"),
        ("upload_id",             "license_holders",  "INTEGER"),
        ("upload_id",             "transfer_ledger",  "INTEGER"),
        ("upload_id",             "closures",         "INTEGER"),
        ("upload_id",             "change_history",   "INTEGER"),
        ("transferee",            "closures",         "VARCHAR(100)"),
        ("transfer_region",       "closures",         "VARCHAR(50)"),
    ]

    for col_name, table_name, col_type in new_cols:
        # 각 테이블별 실제 컬럼 체크
        try:
            tbl_cols = {c["name"] for c in inspector.get_columns(table_name)}
        except Exception:
            tbl_cols = set()
        if col_name in tbl_cols:
            continue  # 이미 있는 컬럼 스킵
        # 컬럼별 독립 트랜잭션
        try:
            with engine.begin() as conn:
                if is_sqlite:
                    conn.execute(text(
                        f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}"))
                else:
                    conn.execute(text(
                        f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {col_name} {col_type}"))
            logger.info(f"마이그레이션 완료: {table_name}.{col_name} 추가")
        except Exception as e:
            logger.warning(f"마이그레이션 스킵 ({table_name}.{col_name}): {e}")

try:
    _run_migrations()
except Exception as e:
    logger.warning(f"마이그레이션 경고 (무시): {e}")

# 변경이력 change_type 자동 재정규화 (기존 '기타' 데이터 수정)
def _renormalize_change_types():
    from app.database import SessionLocal as _SL
    from app import models as _m
    from app.routers.change_history import normalize_change_type as _norm_ct
    db = _SL()
    try:
        records = db.query(_m.ChangeHistory).filter(
            _m.ChangeHistory.deleted_at.is_(None),
        ).all()
        updated = 0
        for rec in records:
            probe_texts = [
                rec.change_type or '',
                rec.memo or '',
                rec.before_value or '',
                rec.after_value or '',
            ]
            if isinstance(rec.raw_data, dict):
                for k in ('비고', '변경내용', '변경유형', '구분', '변경종류'):
                    v = rec.raw_data.get(k, '')
                    if v: probe_texts.append(str(v))
            for txt in probe_texts:
                if txt and txt.strip():
                    detected = _norm_ct(txt)
                    if detected and detected not in ('기타', ''):
                        if detected != rec.change_type:
                            rec.change_type = detected
                            updated += 1
                        break
        if updated:
            db.commit()
            logger.info(f"변경이력 재정규화: {updated}건 업데이트")
    except Exception as e:
        logger.warning(f"변경이력 재정규화 오류 (무시): {e}")
    finally:
        db.close()

try:
    _renormalize_change_types()
except Exception as e:
    logger.warning(f"변경이력 재정규화 경고: {e}")

app = FastAPI(title="강원도 개인소형화물협회 업무관리 시스템", version="3.0.0")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

app.include_router(auth.router,           prefix="/api/auth",           tags=["인증"])
app.include_router(candidates.router,     prefix="/api/candidates",     tags=["예정자"])
app.include_router(members.router,        prefix="/api/members",        tags=["회원"])
app.include_router(transfer_ledger.router,prefix="/api/transfer-ledger",tags=["양도양수대장"])
app.include_router(closures.router,       prefix="/api/closures",       tags=["폐지현황"])
app.include_router(change_history.router, prefix="/api/change-history", tags=["변경이력"])
app.include_router(allocation.router,     prefix="/api/allocation",     tags=["부과대수"])
app.include_router(dashboard.router,      prefix="/api/dashboard",      tags=["대시보드"])
app.include_router(reports.router,        prefix="/api/reports",        tags=["보고서"])
app.include_router(excel.router,          prefix="/api/excel",          tags=["엑셀"])
app.include_router(admin.router,          prefix="/api/admin",          tags=["관리자"])

static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.on_event("startup")
async def startup():
    # startup에서는 최소 작업만 (Healthcheck 즉시 통과 필요)
    db = SessionLocal()
    try:
        create_default_admin(db)
        # ⚠️ backfill_transfer_names 제거: 4591건 UPDATE가 startup을 blocking해서
        # Healthcheck 실패 → Railway 배포 실패 원인
        # 필요 시 /api/admin/db-status 확인 후 수동 실행
    except Exception as e:
        logger.warning(f"startup 오류 (무시): {e}")
    finally:
        db.close()


@app.get("/login")
def login_page():
    return FileResponse(os.path.join(static_dir, "login.html"))


@app.get("/")
def index():
    return FileResponse(os.path.join(static_dir, "index.html"))


@app.get("/{p:path}")
def catch_all(p: str):
    if p.startswith(("api/", "static/")):
        from fastapi import HTTPException
        raise HTTPException(404)
    return FileResponse(os.path.join(static_dir, "index.html"))
