로그인 화면만 변경 - 3테마 패치
기준: 2026-08-29 현재 폐업관리 목록 정리본

[변경 범위]
- 로그인 화면만 변경
- 로그인 후 수납/미수금/폐업관리/연락관리 화면 디자인과 기능은 변경하지 않음
- API/DB/수납/폐업/문자 로직 변경 없음

[로그인 화면]
- 협회명: 강원특별자치도개인소형화물협회
- 제목: 회원관리 시스템
- 입력: 아이디 / 비밀번호
- 비밀번호 보기 버튼
- 상태: 비밀번호 설정됨 · DB 정상

[테마 3개만]
1. 한교동
2. 헬로키티
3. 깔끔한 화이트

[테마 저장]
- 브라우저 localStorage 사용
- 아이디를 입력하고 테마를 선택하면 해당 아이디별 선택값을 기억
- 같은 브라우저에서 다음 로그인 시 해당 아이디의 로그인 테마 복원
- 로그인 이후 메인 화면에는 테마를 적용하지 않음

[GitHub 덮어쓰기 파일]
app/static/receivables.html
app/static/receivables.css
app/static/receivables.js
app/static/theme/hangyodon.png
app/static/theme/hello_kitty.png

위 경로 그대로 덮어쓴 뒤 Commit/Deploy 하면 됩니다.
