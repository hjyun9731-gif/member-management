V23 통합 SaaS 미수금 대시보드 UI 패치
기준: 2026-09-01

덮어쓸 파일
- app/static/receivables.html
- app/static/receivables.css
- app/static/receivables.js

변경 범위
- V10~V22로 누적된 대시보드 전용 CSS override 블록 제거 후 V23 단일 블록으로 통합
- 데스크톱 KPI 4개 한 줄
- 데스크톱 차트 2열 x 2행 유지 (900px 이하에서 1열)
- 가짜 사선 장식 제거
- 실제 API history 배열을 사용하는 KPI sparkline 추가
- 월별 수납/부과율 차트에 100% 기준선 추가
- 기간비교 요약을 4개 지표 구조로 정리
- 월별 성과 요약 2x2
- 미수회원 조회 결과 수에 따라 자동 높이
- 엑셀 다운로드 연노랑 유지

변경하지 않음
- API endpoint
- PostgreSQL / DB schema / 데이터
- app/routers/receivables.py
- app/receivables_models.py
- app/data/legacy_receivables_2026.json
- 미수금/선납/누적부과/컷오버/신규부과 계산 로직

검사
- node --check receivables.js 통과
- HTML id 중복 없음
- V20 원본부터 존재하던 optional legacy DOM 참조 6개는 그대로이며 이번 변경으로 새 누락 ID를 만들지 않음
- CSS 중괄호 개수 일치
