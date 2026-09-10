폐업현황 UI 실제 수정본 - 2026-09-10

이번 수정은 기존 코드를 실제로 확인한 뒤 만든 최소 패치입니다.

실제 확인된 기존 코드:
- app/static/app.js 의 renderClosures()
- 현재 양수인: r.transferee
- 현재 이관지역: r.transfer_region
- 폐업사유: r.reason
- 기존 폐업현황 표에는 양수인/이관지역은 보이지만 폐업사유가 빠져 있었음

수정 내용:
1. 폐업사유 컬럼 항상 표시
2. 양수인/이관지역 기본 숨김
3. 조회/초기화 옆 '양수인/이관지역 보기' 버튼
4. 버튼 누르면 두 컬럼을 동시에 표시
5. 표시 시 버튼 문구 '양수인/이관지역 숨기기'
6. 다시 누르면 두 컬럼 숨김
7. 기존 검색/수정/삭제/페이지네이션 유지
8. app.js 캐시 문제 방지를 위해 index.html의 app.js 버전을
   v=20260910closure1 로 변경

적용 방법:
- 이 ZIP 압축을 풉니다.
- '적용하고_배포.cmd'를 더블클릭합니다.

기본 저장소 경로:
C:\Users\PC\Documents\GitHub\member-management

안전장치:
- main 브랜치가 아니면 중단
- app.js/index.html에 미커밋 변경이 있으면 중단
- git pull --ff-only 실패 시 중단
- 현재 코드에 패치가 정확히 맞지 않으면 수정 전 중단
- JS 문법 또는 git diff 검사 실패 시 자동 원상복구
- app.js/index.html 두 파일만 커밋
