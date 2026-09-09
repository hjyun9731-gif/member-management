#!/usr/bin/env python3
from pathlib import Path
import shutil

ROOT = Path.cwd()
MAIN = ROOT / "app" / "main.py"
GUARD_SRC = Path(__file__).resolve().parent / "app" / "memo_sanitizer.py"
GUARD_DST = ROOT / "app" / "memo_sanitizer.py"
MARK = "import app.memo_sanitizer  # RECEIVABLES MEMO POLLUTION GUARD"

if not MAIN.exists():
    raise SystemExit("오류: app/main.py를 찾을 수 없습니다. member-management 저장소 루트에서 실행하세요.")
if not GUARD_SRC.exists():
    raise SystemExit("오류: 패치 파일 app/memo_sanitizer.py를 찾을 수 없습니다.")

backup = MAIN.with_name("main.py.before_receivables_memo_fix")
if not backup.exists():
    shutil.copy2(MAIN, backup)

# 소스와 대상이 같은 경우 copy2 생략
if GUARD_SRC.resolve() != GUARD_DST.resolve():
    GUARD_DST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(GUARD_SRC, GUARD_DST)

text = MAIN.read_text(encoding="utf-8")
if MARK not in text:
    anchor = "import app.models as _models"
    if anchor in text:
        text = text.replace(anchor, anchor + "\n" + MARK, 1)
    else:
        anchor = "from app.database import Base, engine, SessionLocal, DATABASE_URL"
        if anchor not in text:
            raise SystemExit("오류: main.py 안전 삽입 위치를 찾지 못했습니다. 파일은 변경하지 않았습니다.")
        text = text.replace(anchor, anchor + "\n" + MARK, 1)
    MAIN.write_text(text, encoding="utf-8")

print("적용 완료")
print("추가: app/memo_sanitizer.py")
print("수정: app/main.py (import 1줄)")
print("백업:", backup)
print("배포 후 기존 오염 비고 자동정리 + 향후 재저장 차단")
