[최종 디자인 패치 - 기능 변경 없음]

변경 파일
1) app/static/index.html
   - 기존 app.js는 그대로 사용
   - 최종 CSS/JS 로드 태그만 추가
   - 화면 표기 협회명만 "강원특별자치도개인소형화물협회"로 정정

신규 파일
2) app/static/design-final-20260829.css
3) app/static/design-final-20260829.js

절대 변경하지 않은 것
- app/static/app.js
- Python 파일 전체
- API POST/PUT/DELETE 처리
- DB 모델/테이블/데이터
- 예정자 저장/수정/등록/삭제
- 검색/필터/페이지네이션
- 양도양수 처리
- 수납/미수금 처리
- 기한관리 등록/수정/완료/삭제

상단 요약 카드의 JS는 기존 GET API만 읽습니다.
- 회원통계: GET /api/dashboard/full-stats
- 수납요약: GET /api/receivables/summary
- 기한관리: GET /api/deadlines/summary, GET /api/deadlines
- 최근업무: GET /api/dashboard/recent-by-type

적용
압축을 풀고 GitHub 저장소 최상단에 폴더 구조 그대로 덮어쓰기 후 재배포합니다.
