DESIGN ONLY V11 — 승인 레퍼런스 비율 재작성

적용:
- app/static/index.html
- app/static/design-dashboard-v11-20260830.css
- app/static/design-dashboard-v11-20260830.js

기능 보존:
- app.js 미포함 / 미수정
- Python/API/DB 미포함 / 미수정
- 저장/수정/등록/삭제/검색/페이지네이션 이벤트 미변경
- V11 JS는 GET 조회 + 표시용 DOM/CSS 클래스 처리만 수행

핵심 수정:
- header/sub-bar position 강제 정상화 (뒤집혀 보이던 순서 수정)
- 페이지 제목/날짜가 앱 rerender 후에도 복원되도록 decorator 안정화
- 상단 4카드 높이 286px / 승인 레퍼런스 비율
- 기한관리 4줄이 카드 안에서 잘리지 않도록 재설계
- 차종 250px / 유종 150px
- '없으면 미가입' 화면 문구 숨김
- 예정자 목록 주민등록번호 표시 / 소속업체 숨김
- 좌우 업무영역 약 37.5 : 62.5
