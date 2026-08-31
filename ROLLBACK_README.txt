긴급 기능복구 패치 2026-08-31

이 패치는 마지막 RECEIVABLES_FINAL_AUDIT_CLOSURE 패치에서 잘못 건드린
업무관리시스템의 폐업현황 기능 변경만 되돌립니다.

덮어쓰기:
- app/routers/closures.py        : 기존 폐업현황 라우터 복구
- app/static/index.html          : closure-membership-v1 강제주입 제거
- app/static/closure-membership-v1.js : 무동작(no-op) 처리

건드리지 않음:
- app/static/app.js
- 기존 회원관리/예정자/양도양수/기한관리 기능
- DB 스키마
- 수납/미수금 패치 파일(receivables.py/js/html/css/json)

적용 후 브라우저 Ctrl+F5 권장.
