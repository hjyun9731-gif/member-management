from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./member_management.db")

# Railway는 postgres:// 형식으로 제공 → postgresql:// 로 변환
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

_is_sqlite = DATABASE_URL.startswith("sqlite")

if _is_sqlite:
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
else:
    # PostgreSQL: 동시 접속 대비 연결 풀 설정
    engine = create_engine(
        DATABASE_URL,
        pool_size=5,        # 기본 연결 수 (3명 동시 사용에 충분)
        max_overflow=10,    # 최대 추가 연결
        pool_pre_ping=True, # 끊긴 연결 자동 감지
        pool_recycle=1800,  # 30분마다 연결 갱신
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
