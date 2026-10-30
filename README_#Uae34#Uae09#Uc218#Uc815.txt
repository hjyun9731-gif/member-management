2026-10-08 reconcile API 404 긴급수정

현재 로그에서 확인된 문제:
- 서버와 기존 /api/receivables/* 는 정상 200
- /api/receivables/reconcile-20261008/giro-targets 만 404
- 원인: app.main의 /{p:path} catch-all 라우트가 먼저 등록되어 있고,
  railway_entry에서 나중에 include_router()한 reconcile API가 catch-all 뒤에 들어가 404로 가로채짐.

업로드:
이 ZIP을 풀고 아래 3개 파일을 GitHub main의 동일 경로에 덮어쓰기.
- app/railway_entry.py
- app/routers/receivables_reconcile_20261008.py
- app/data/receivables_reconcile_20261008_final.json

삭제할 파일 없음. DB/.env/requirements 변경 없음.

수정 내용:
- railway_entry가 /api/receivables/reconcile-20261008/* 요청을 app.main보다 먼저 전용 FastAPI 라우터로 전달.
- 기존 회원관리/수납 API에는 영향 없음.
- 백그라운드 apply_once 로직은 그대로 유지.

재배포 후 확인할 로그/URL:
1) [railway-entry] real app ready ...
2) [railway-entry] receivables 20261008 FINAL reconcile: status=...
3) /api/receivables/reconcile-20261008/giro-targets -> 200 (로그인 상태)
4) /api/receivables/reconcile-20261008/status -> 200 (로그인 상태)
5) /receivables에서 이민행/지로희망/종료자 확인
