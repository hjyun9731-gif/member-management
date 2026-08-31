DESIGN ONLY V13 (2026-08-30)

Requested changes:
- 회원통계/수납요약 카드의 빈약한 단순 배치를 폐기하고 KPI 패널 + 미니 통계 + 실제 값 기반 시각 계층으로 개선
- 회원통계: 전체회원 / 협회가입 / 미가입 / 가입률 표시
- 수납요약: 수납액 / 활성 미수금 / 미수회원 / 선납 표시
- 카드 아이콘을 문자기호에서 inline SVG line icon으로 변경
- 회원통계 하단 버튼 클릭 시 "보고/집계"를 연 뒤 "회원대시보드" 서브탭을 찾아 클릭하도록 연결
- 기존 Kakao Small Sans 및 진한 글자색 유지
- 자격증명발급일자 / 자격증명발급번호 표기 유지

Safety:
- app.js NOT included and NOT modified
- Python/backend/database files NOT included
- Existing save/edit/register/delete/search/page logic untouched
- Decorator continues to use GET only for dashboard reads
- The only added interaction is UI navigation through existing report/sub-tab click handlers
