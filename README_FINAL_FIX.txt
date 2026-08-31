FINAL_FIX_TRANSFER_ARREARS_LEDGER_20260831_GITHUB
=================================================

대상: hjyun9731-gif/member-management / main / Railway production

이번 ZIP은 아래 6개 파일만 덮어쓰는 안전 패치입니다.

1. app/static/app.js
2. app/static/styles.css
3. app/static/receivables.html
4. app/static/receivables.js
5. app/static/receivables.css
6. app/routers/receivables.py

[양도양수대장]
- 행 왼쪽 ▸ 상세펼침 화살표 열 완전 삭제
- 상단 '양도자 전체보기' 기능은 유지
- 전체 th/td 가운데 정렬
- 주소/비고는 한 줄 말줄임 + 기존 title 전체보기 유지
- 관리열 210px 고정
- 수정 / 등록완료 / 삭제가 한 줄에서 잘리지 않게 표시
- 1920px PC 기준 표 전체 폭 재배분
- 양도자 전체보기 시 보조행은 전체 폭 16열 colspan

[미수금현황]
- /api/receivables/monthly-analysis 읽기 API 포함
- 전월말 미수 / 현재 총 미수 / 이번 달 수납 / 증가 / 감소 / 순증감 실제 계산값 표시
- API 오류가 생기면 '-'로 숨기지 않고 '조회오류' 표시
- KPI/전월비 분석 영역 높이와 여백 축소
- 회원을 선택하기 전에는 빈 오른쪽 상세패널을 숨기고 미수회원 목록을 전체 폭으로 표시
- 회원 선택 시에만 목록 + 상세 2단 화면으로 전환

[2026 월별 장부]
- 화면 컬럼: 월 / 월 부과액 / 입금액 / 입금일 / 추가입금 / 금액수정 / 월말 잔액
- '자동부과' 별도 컬럼 삭제 (자동부과 로직은 삭제하지 않음)
- 프로그램 자동부과가 있는 달은 '월 부과액'에 합산 표시
- '반영 잔액' 중복 컬럼 삭제
- 월말잔액은 최종 계산 잔액(current_arrears) 하나로 표시
- 원본 미수금 셀이 빈 달은 전월잔액 + 월부과 - 입금 방식으로 복원
  예: 3월 1,160,000원, 4월 부과 10,000원/입금 0원이면 4월말 1,170,000원
- 금액수정은 실제 입금과 분리 표시

[보호 범위]
- DB 스키마 변경 없음
- receivable_* 테이블 DROP/ALTER 없음
- 기존 회원등록/수정/폐업/양도양수/자격증명/월마감 API 구조 변경 없음
- 자동부과 자체는 유지하며 '자동부과'라는 중복 표시 열만 제거

[적용]
저장소 루트에서 ZIP 내용을 그대로 덮어쓴 뒤:

  git add app/static/app.js app/static/styles.css app/static/receivables.html app/static/receivables.js app/static/receivables.css app/routers/receivables.py
  git commit -m "fix transfer ledger and receivables overview"
  git push origin main

Railway 배포 후 브라우저에서 Ctrl+Shift+R 1회 실행.
