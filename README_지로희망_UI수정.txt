2026-10-08 지로희망 UI 정상화 패치

현재 Railway production의 UI recovery + MONTHLY V4 상태를 유지하면서 지로희망 표시/조회만 수정합니다.

변경 파일
- app/static/receivables.html
- app/static/receivables.js
- app/static/receivables.css
- app/routers/receivables_reconcile_20261008.py

변경 내용
1. 회원 이름 뒤에 '지로희망' 텍스트를 이어 붙이지 않음.
2. 회원 목록에 별도 '지로' 열을 추가하고 희망 대상만 노란 '희망' 배지로 표시.
3. 검색창으로 '지로'를 검색하는 방식 대신 '지로 전체 / 지로희망만' 전용 필터 추가.
4. 전용 /api/receivables/reconcile-20261008/giro-members API에서 최종원장 지로희망 대상만 조회.
5. 검색창에 '지로' 또는 '지로희망'을 입력해도 자동으로 전용 필터로 전환.
6. 지로희망은 회원상태/미수금/자동부과 계산에 영향 없음.
7. 기존 MONTHLY V4 장부 계산과 UI recovery 기능은 유지.

배포 후 확인
- /static/receivables.css?v=20261008-giro-ui-v5 200
- /static/receivables.js?v=20261008-giro-ui-v5 200
- /api/receivables/reconcile-20261008/giro-members?... 200
- 필터 '지로희망만' 선택 시 최종원장 대상만 조회
