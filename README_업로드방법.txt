2026-10-08 월별장부 전수정정 V2

이전 패치 문제:
- legacy_balance만 맞춰 왼쪽 현재잔액과 오른쪽 월별장부가 서로 달라질 수 있었음.

이번 V2:
- 최종 엑셀의 2026년 1~9월 월별 부과/입금/입금일/월말잔액을 기준으로 legacy_months를 다시 구성
- 9월말 잔액을 legacy_balance 기준점으로 재설정
- 9월까지 기존 동적 charge/payment는 중복계산 방지를 위해 보존하되 중립화/취소(삭제하지 않음)
- 10월 이후 실제 수납/부과는 보존
- 이민행 97자1025는 9/1 합동입금 60,000원, 9월말 0원으로 명시 반영
- 한인교 그룹의 격월 10,000원 납부 형태도 엑셀 그대로 표시

GitHub main에 아래 경로 그대로 덮어쓰기:
app/railway_entry.py
app/routers/receivables_reconcile_20261008.py
app/data/receivables_reconcile_20261008_final.json
app/static/receivables.js
app/static/receivables.html

정상 로그:
[railway-entry] real app ready ...
[railway-entry] receivables 20261008 MONTHLY V2 reconcile: status=...

확인:
- 한인교 검색 후 박달원/신명한/정의진/한인교의 월별 장부가 격월 10,000원 납부로 표시되는지
- 이민행 97자1025: 9월 입금 60,000원 / 9월말 0원 / 10월 현재 5,000원
