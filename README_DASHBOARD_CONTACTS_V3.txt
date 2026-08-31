member-management 수납/미수금 UI 개선 V3
기준일: 2026-08-31

[이번 반영]
1. 미수금현황 → 미수금 대시보드
- 활성 미수금 / 미수회원 / 1인 평균 / 연락기록률 / 10만원 이상 / 폐업 미수 / 선납 요약
- 지역별 미수 TOP 8 (금액, 회원수, 전체비중, 연락률)
- 계정별 비교 (협회비/관리비/70세)
- 미수금 구간별 분포
- 지역 A/B 선택 비교 (미수금, 회원수, 평균, 연락률, 전체 비중)
- 연락상태 분포
- 고액 미수 TOP 10, 클릭 시 회원 상세 열기
- 기존 전월 대비 미수금 변동/6개월 추이는 그대로 유지
- 대시보드 아래 미수회원 상세 조회/월별 장부 유지

2. 연락관리
- 연락 기록이 1건 이상 존재하는 회원만 기본 표시
- 최근 연락일 / 연락상태 / 현재잔액 / 부과상태를 목록에서 동시 확인
- 상단 연락관리 요약: 연락기록 회원, 연락완료, 문자발송, 재연락 필요, 부재, 최근 7일 연락
- 회원 선택 시 기존 상세/연락이력 기능 유지

3. Excel 다운로드 색상
- 진한 초록/청록색 제거
- 수납/미수금 Excel 버튼과 기존 시스템 bxl Excel 버튼 모두 연한 노란색으로 변경

4. 상단/하위 메뉴 Bold
- 회원관리 / 수납·미수금 / 인허가·변경 / 보고·집계 / 기한관리 / 엑셀업로드 / 명단추출 포함 category nav 굵게
- 예정자·양도양수 / 개인회원 / 택배회원 포함 sub tab 굵게

5. 기존 기능 보호
- DB DROP/ALTER 없음
- 기존 수납/부과/폐업/양도/이관 로직 변경 없음
- 대시보드는 읽기 전용 집계 API 추가
- 연락관리 필터는 latest contact log 존재 여부만 추가

[덮어쓸 파일]
- app/routers/receivables.py
- app/static/receivables.html
- app/static/receivables.js
- app/static/receivables.css
- app/static/styles.css
- app/static/app.js (이전 양도양수 표 수정 유지)

[검증]
- python -m py_compile app/routers/receivables.py : PASS
- node --check app/static/receivables.js : PASS

배포 후 Chrome에서 Ctrl + Shift + R 1회 권장.
