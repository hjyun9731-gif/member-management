from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
import os
import secrets
import sys

from app.database import get_db
from app import models


_KNOWN_WEAK_SECRETS = {"change-this-secret-key-in-production-2024", "changeme", "change-me", "secret", "secret-key", "your-secret-key"}
_MIN_SECRET_LENGTH = 32


def security_status(env=None) -> dict:
    """보안 설정 점검 결과. 설정 여부·길이 구간·기본값 여부만 담고 비밀값은 절대 포함하지 않는다."""
    env = os.environ if env is None else env
    key = str(env.get("SECRET_KEY", "") or "").strip()
    weak = key.lower() in _KNOWN_WEAK_SECRETS or key.lower().startswith("change-this")
    return {
        "production": bool(env.get("RAILWAY_ENVIRONMENT")),
        "secret_key_configured": bool(key) and not weak,
        "secret_key_is_known_default": weak,
        "secret_key_length_ok": len(key) >= _MIN_SECRET_LENGTH and not weak,
        "database_url_configured": bool(str(env.get("DATABASE_URL", "")).strip()),
    }


def _resolve_secret_key() -> str:
    """SECRET_KEY를 환경변수에서 읽는다. 고정 기본값은 더 이상 제공하지 않는다.

    운영 환경(RAILWAY_ENVIRONMENT가 설정됐거나 DATABASE_URL이 PostgreSQL인 경우)에서는
    SECRET_KEY 미설정 시 기동 자체를 실패시킨다 — 고정 기본값으로 토큰이 위조되는 것을 막기 위함.
    로컬 개발/테스트(SQLite, 환경변수 모두 없음)에서는 프로세스 수명 동안만 유효한 임시 키를
    생성해 편의성을 유지하되, 재시작 시 기존 토큰은 모두 무효화된다.
    """
    key = (os.getenv("SECRET_KEY") or "").strip()
    if key and (key.lower() in _KNOWN_WEAK_SECRETS or key.lower().startswith("change-this")):
        # 과거 코드에 하드코딩돼 있던 기본값 등은 '설정되지 않은 것'으로 취급한다(운영에서는 시작 거부).
        key = ""
    if key:
        if len(key) < _MIN_SECRET_LENGTH:
            print(f"[auth] 경고: SECRET_KEY 가 {_MIN_SECRET_LENGTH}자보다 짧습니다. 더 긴 무작위 값으로 교체하는 것을 권장합니다.", file=sys.stderr)
        return key

    # RAILWAY_ENVIRONMENT는 Railway가 실제 배포 시에만 자동으로 주입하는 값이다.
    # PostgreSQL 자체는 로컬/CI 테스트에서도 흔히 쓰이므로 production 판정 근거로 삼지 않는다.
    is_production = bool(os.getenv("RAILWAY_ENVIRONMENT"))
    if is_production:
        raise RuntimeError(
            "SECRET_KEY 환경변수가 설정되지 않았습니다. "
            "운영 환경에서는 고정 기본값을 사용할 수 없습니다. "
            "`python -c \"import secrets; print(secrets.token_hex(32))\"` 로 생성한 값을 "
            "SECRET_KEY 환경변수로 설정하세요."
        )
    generated = secrets.token_hex(32)
    print(
        "[auth] 경고: SECRET_KEY 환경변수가 설정되지 않아 임시 키를 생성했습니다. "
        "이 프로세스가 재시작되면 기존 로그인 토큰은 모두 무효화됩니다. "
        "운영 배포 전에는 반드시 SECRET_KEY를 환경변수로 설정하세요.",
        file=sys.stderr,
    )
    return generated


SECRET_KEY = _resolve_secret_key()
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 1440))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def authenticate_user(db: Session, username: str, password: str):
    user = db.query(models.User).filter(
        models.User.username == username,
        models.User.deleted_at.is_(None)
    ).first()
    if not user or not verify_password(password, user.password_hash):
        return None
    return user


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> models.User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="인증 정보가 올바르지 않습니다.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(models.User).filter(
        models.User.username == username,
        models.User.deleted_at.is_(None)
    ).first()
    if user is None:
        raise credentials_exception
    return user


async def require_admin(current_user: models.User = Depends(get_current_user)) -> models.User:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="관리자만 이 작업을 수행할 수 있습니다."
        )
    return current_user


def create_default_admin(db: Session):
    """admin 계정 없으면 생성. 동시 실행 race condition은 UniqueViolation으로 처리."""
    try:
        existing = db.query(models.User).filter(models.User.username == "admin").first()
        if not existing:
            admin = models.User(
                username="admin",
                password_hash=get_password_hash("admin1234"),
                role="admin",
                full_name="관리자"
            )
            db.add(admin)
            db.commit()
    except Exception:
        db.rollback()  # 중복 등 오류 발생 시 rollback만 (admin은 이미 존재)


STAFF_ROLES = {"admin", "staff"}


def staff_write_ok(fn):
    """일반 직원(staff)이 쓸 수 있는 '일상 업무 입력'임을 표시한다.

    admin_for_writes 는 변경 요청(POST/PUT/PATCH/DELETE)을 기본적으로 관리자 전용으로 막고,
    이 표시가 붙은 엔드포인트만 직원에게 연다 → 앞으로 새로 추가되는 변경 API는 자동으로 관리자 전용이다.
    """
    fn._staff_write_ok = True
    return fn


async def admin_for_writes(request: Request, db: Session = Depends(get_db)):
    """수납·미수금 모듈의 변경 요청 권한: 로그인 필수, 관리자는 전부, 직원은 @staff_write_ok 표시된 엔드포인트만.

    조회(GET/HEAD/OPTIONS)는 각 엔드포인트의 기존 인증을 따른다(페이지 HTML 자체는 공개).
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    token = await oauth2_scheme(request)           # 토큰이 없으면 401
    user = await get_current_user(token=token, db=db)
    if user.role == "admin":
        return user
    endpoint = getattr(request.scope.get("route"), "endpoint", None)
    if user.role in STAFF_ROLES and getattr(endpoint, "_staff_write_ok", False):
        return user
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="관리자만 이 작업을 수행할 수 있습니다.")
