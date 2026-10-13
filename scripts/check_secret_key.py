"""SECRET_KEY 등 보안 환경변수 점검 — 비밀값을 절대 출력하지 않는다(설정 여부·길이 구간·기본값 여부만).

Railway 에 설정된 변수를 그대로 사용해 점검하려면(로컬 PC 에서):
    railway link                      # 프로젝트/환경(production)/서비스 선택(최초 1회)
    railway run python -m scripts.check_secret_key

종료 코드: 0 = 안전, 2 = 서버가 시작을 거부하는 상태(미설정/기본값), 1 = 경고 있음(짧은 키 등)
"""
import json
import sys


def main() -> int:
    try:
        from app.auth import security_status
    except RuntimeError as exc:           # SECRET_KEY 미설정/기본값 → 운영에서는 서버가 시작되지 않는다
        print(json.dumps({"ok": False, "server_would_start": False, "reason": str(exc)[:120]}, ensure_ascii=False, indent=2))
        return 2
    st = security_status()
    problems = []
    if not st["secret_key_configured"]:
        problems.append("SECRET_KEY 미설정 또는 알려진 기본값")
    elif not st["secret_key_length_ok"]:
        problems.append("SECRET_KEY 가 32자보다 짧음(교체 권장)")
    if not st["database_url_configured"]:
        problems.append("DATABASE_URL 미설정")
    print(json.dumps({"ok": not problems, "server_would_start": True, "status": st, "problems": problems},
                     ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
