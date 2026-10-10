2026-10-08 월별장부 V4 최종수정

현재 Railway에 올라가 있는 UI recovery 상태를 유지하면서 월별 이월/잔액 계산만 수정합니다.
새 UI로 갈아엎는 패치가 아닙니다.

GitHub main에 아래 5개 파일만 경로 그대로 덮어쓰세요.

1. app/railway_entry.py
2. app/routers/receivables_reconcile_20261008.py
3. app/data/receivables_reconcile_20261008_final.json
4. app/static/receivables.html
5. app/static/receivables.js

건드리지 않는 파일:
- app/static/receivables.css  (현재 UI recovery 그대로 유지)
- app/static/sw.js            (현재 UI recovery 그대로 유지)
- app/routers/receivables.py
- app/receivables_models.py
- 기존 회원관리/문자/폐업/월마감 기능

V4 핵심:
- V2/V3 적용상태와 무관한 새 상태키로 반드시 1회 재정합
- 1~9월은 최종 수정원장으로 baseline 재설정
- 9월말 확정잔액을 10월 시작점으로 사용
- 10월 이후 실제 charge/payment만 이어서 계산
- 기존 10월 행과 authoritative ledger를 합치지 않음
- 현재 목록 행도 V4 authoritative balance로 덮어 보여줌
- 전체 회원에 공통 적용하며 특정 회원 하드코딩 없음
- 재실행해도 pre-10월 payment/charge 중복 생성 없음

배포 후 Railway 로그에서 확인:
[railway-entry] receivables 20261008 MONTHLY V4 repair: status=applied ...

브라우저 로그/Network에서 확인:
/static/receivables.js?v=20261008-monthly-v4
/api/receivables/reconcile-20261008/balances?... -> 200
/api/receivables/reconcile-20261008/ledger/<member_id> -> 200

관리자 검증 API:
GET /api/receivables/reconcile-20261008/audit
- ok=true가 목표
- source_recurrence_mismatches=[]
- baseline_mismatches=[]
- residual_pre_oct_charges=0
- residual_pre_oct_live_payments=0
- db_formula_mismatches=[]

대표 회귀검증:
이민행 / 강원97자 1025 / member_id=8048
9월말 0원 -> 10월 부과 5,000원 -> 현재 5,000원
(이 회원만 하드코딩한 것이 아니라 전체 공통 로직 결과입니다.)
