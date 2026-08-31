미수금 대시보드 디자인 전용 패치 V10

변경 파일은 딱 2개입니다.
1. app/static/receivables.css
2. app/static/receivables.html  (CSS 캐시 버전 문자열만 변경)

변경하지 않은 것:
- app/static/receivables.js
- app/routers/receivables.py
- DB / API / 계산식 / 데이터
- 수납처리
- 폐업관리
- 연락관리
- 회원조회 기능
- 상단 메뉴 / 탭 구조

적용 내용:
- 미수금 대시보드 영역만 레퍼런스처럼 카드 중심의 정돈된 레이아웃으로 변경
- 좌측 사이드바 추가하지 않음
- 월별 추이 + 부과/수납 차트를 PC에서 좌우 2열 배치
- KPI 카드 높이/간격 정리
- 월별 성과표 연보라 헤더 + 전 셀 가운데 정렬
- 기존 브라우저 페이지 스크롤 유지
