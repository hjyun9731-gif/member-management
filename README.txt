업로드할 파일은 아래 2개입니다.

GitHub 경로
1. app/static/app.js
2. app/static/index.html

반영 내용
- 폐업사유(reason) 컬럼 항상 표시
- 양수인(transferee), 이관지역(transfer_region) 기본 숨김
- 조회/초기화 옆 '양수인/이관지역 보기' 버튼
- 버튼 클릭 시 두 컬럼 전체 펼침/접힘
- 표시 중 버튼 문구 '양수인/이관지역 숨기기'
- 기존 검색/수정/삭제/페이지네이션 로직 유지
- index.html의 app.js 캐시 버전 갱신

백엔드/DB 파일은 수정하지 않습니다.
