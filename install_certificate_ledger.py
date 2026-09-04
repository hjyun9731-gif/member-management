"""자격증명 발급대장만 추가하는 안전 설치기.

실행 위치: 기존 member-management 저장소 최상단
수정되는 기존 파일: app/main.py, app/routers/candidates.py, app/static/index.html
각 파일에는 연결 코드만 삽입하며 기존 줄을 삭제하지 않는다.
"""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent


def add_after(text: str, anchor: str, addition: str, label: str) -> str:
    if addition.strip() in text:
        return text
    if anchor not in text:
        raise RuntimeError(f"{label}: 연결 위치를 찾지 못했습니다. 아무 파일도 수정하지 않았습니다.")
    return text.replace(anchor, anchor + addition, 1)


def main() -> int:
    targets = {
        ROOT / "app/main.py": None,
        ROOT / "app/routers/candidates.py": None,
        ROOT / "app/static/index.html": None,
    }
    missing = [str(path.relative_to(ROOT)) for path in targets if not path.exists()]
    if missing:
        print("설치 중단: 기존 프로젝트 파일을 찾을 수 없습니다: " + ", ".join(missing))
        return 1

    required_new = [
        ROOT / "app/certificate_ledger_models.py",
        ROOT / "app/services/certificate_ledger_service.py",
        ROOT / "app/routers/certificate_ledger.py",
        ROOT / "app/static/certificate-ledger.js",
        ROOT / "app/static/certificate-form.html",
    ]
    missing_new = [str(path.relative_to(ROOT)) for path in required_new if not path.exists()]
    if missing_new:
        print("설치 중단: 추가 모듈 파일이 없습니다: " + ", ".join(missing_new))
        return 1

    main_path = ROOT / "app/main.py"
    main_text = main_path.read_text(encoding="utf-8")
    main_text = add_after(
        main_text,
        "import app.receivables_models as _receivables_models\n",
        "\n# === CERTIFICATE LEDGER ADD-ONLY ===\nfrom app.routers import certificate_ledger\nimport app.certificate_ledger_models as _certificate_ledger_models\n",
        "app/main.py 모델 import",
    )
    main_text = add_after(
        main_text,
        "app.include_router(receivables.router)\n",
        "\n# 자격증명 발급대장: 신규 API만 추가\napp.include_router(certificate_ledger.router)\n",
        "app/main.py 라우터 연결",
    )

    candidates_path = ROOT / "app/routers/candidates.py"
    candidates_text = candidates_path.read_text(encoding="utf-8")
    hook = (
        "\n    # 자격증명 발급대장 연결: 기존 회원등록 결과에는 영향 없음\n"
        "    try:\n"
        "        from app.services.certificate_ledger_service import mark_candidate_approved\n"
        "        mark_candidate_approved(db, cid, member.id, body.approval_date, _)\n"
        "    except Exception:\n"
        "        # 연결 실패가 기존 예정자 등록을 취소하거나 500 오류로 바꾸지 않게 분리\n"
        "        pass\n"
    )
    candidates_text = add_after(
        candidates_text,
        "    except Exception as e:\n        raise HTTPException(500, f\"예정자 등록 처리 중 오류가 발생했습니다: {e}\")\n",
        hook,
        "app/routers/candidates.py 자동연동",
    )

    index_path = ROOT / "app/static/index.html"
    index_text = index_path.read_text(encoding="utf-8")
    loader = '<script src="/static/certificate-ledger.js?v=20260904"></script>\n'
    if loader.strip() not in index_text:
        anchor = "</body>"
        if anchor not in index_text:
            raise RuntimeError("app/static/index.html: </body>를 찾지 못했습니다. 아무 파일도 수정하지 않았습니다.")
        index_text = index_text.replace(anchor, loader + anchor, 1)

    # 모든 검사가 끝난 뒤에만 세 파일을 기록한다.
    targets[main_path] = main_text
    targets[candidates_path] = candidates_text
    targets[index_path] = index_text
    for path, text in targets.items():
        path.write_text(text, encoding="utf-8", newline="")

    print("완료: 자격증명 발급대장 연결 코드만 추가했습니다.")
    print("수정된 기존 파일 3개: app/main.py, app/routers/candidates.py, app/static/index.html")
    print("기존 기능 코드 삭제/교체: 0줄")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"설치 중단: {exc}")
        raise SystemExit(1)
