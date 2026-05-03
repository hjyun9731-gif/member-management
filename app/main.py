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

# 테이블 생성 (없는 경우)
try:
    _models.Base.metadata.create_all(bind=engine)
    db_type = "SQLite" if "sqlite" in DATABASE_URL else "PostgreSQL"
    logger.info(f"DB 연결 완료 ({db_type})")
except Exception as e:
    logger.error(f"DB 초기화 오류: {e}")
    raise

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
    db = SessionLocal()
    try:
        create_default_admin(db)
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
