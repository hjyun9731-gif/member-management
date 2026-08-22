# PLANB 기한관리 V5 - 최신 원본 병합

베이스: 사용자가 마지막으로 업로드한 `member-management-main(1).zip`

## 기한관리 반영
- PLANB형 월/주/일 캘린더
- 단일 기한일 / 시작~종료 기간 일정
- 일정별 색상
- 토요일 파란색, 일요일/공휴일 빨간색
- 대한민국 공휴일명 표시 및 대체공휴일 계산

## 명단추출 보호
다음 항목은 최신 업로드본을 그대로 유지함.
- `app/routers/sms.py`
- `app/static/index.html`의 `명단추출` 메뉴
- `app/static/app.js`의 명단추출/SMS 영역 (`const SMS_VARS` 이후)
- `/api/sms/export/all`
- `/api/sms/export/selected`
- 명단 엑셀 다운로드 UI/필터

이 병합은 기한관리 코드 블록과 공휴일 백엔드/CSS/필요 의존성만 수정함.
