DESIGN ONLY V6 — 2026-08-30

Purpose
- Fix dashboard card sizing/clipping and display formatting only.

Changed existing file
- app/static/index.html : only design CSS/JS filenames updated to V6.

Added design files
- app/static/design-dashboard-v6-20260830.css
- app/static/design-dashboard-v6-20260830.js

NOT INCLUDED / NOT MODIFIED
- app/static/app.js
- Python files
- routers / API write logic
- DB/models
- save/edit/register/delete/search handlers

V6 fixes
1. 회원통계 2x2 표가 카드 안에서 잘리지 않도록 규격 재계산.
2. 수납요약의 선납/부과대기까지 모두 카드 안에 표시.
3. 기한관리에서 실제 일정 제목(title) + 대상 + 기한 + D-day 표시.
4. '진행' 숫자는 전체 미완료 일정 수로 계산해 표시.
5. 최근업무 날짜를 YYYY.MM.DD 한 형식으로 통일.
6. 상단 카드 높이와 내부 행 높이를 고정해 화면마다 찌그러지지 않게 조정.
