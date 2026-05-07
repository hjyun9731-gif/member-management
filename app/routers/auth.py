from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import authenticate_user, create_access_token, get_current_user, get_password_hash
from app.schemas import Token, UserCreate, UserResponse
from app import models

router = APIRouter()


@router.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="아이디 또는 비밀번호가 올바르지 않습니다.",
        )
    access_token = create_access_token(data={"sub": user.username, "role": user.role})
    return Token(
        access_token=access_token,
        token_type="bearer",
        username=user.username,
        role=user.role,
        full_name=user.full_name,
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: models.User = Depends(get_current_user)):
    return current_user


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="관리자만 접근 가능합니다.")
    return db.query(models.User).filter(models.User.deleted_at.is_(None)).all()


@router.post("/users", response_model=UserResponse)
async def create_user(
    user_data: UserCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="관리자만 접근 가능합니다.")
    existing = db.query(models.User).filter(models.User.username == user_data.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="이미 존재하는 아이디입니다.")
    user = models.User(
        username=user_data.username,
        password_hash=get_password_hash(user_data.password),
        role=user_data.role,
        full_name=user_data.full_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="관리자만 접근 가능합니다.")
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    if user.username == "admin":
        raise HTTPException(status_code=400, detail="기본 관리자 계정은 삭제할 수 없습니다.")
    import datetime as _dt
    user.deleted_at = _dt.datetime.now(_dt.timezone.utc)
    db.commit()
    return {"message": "삭제되었습니다."}
