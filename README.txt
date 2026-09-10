member-management 폐업현황 UI 패치
기준: 2026-09-10

수정 파일
- app/static/app.js 1개만 수정

반영 내용
1) 폐업현황에 "폐업사유" 컬럼 상시 표시
   - 실제 API 필드 reason 사용
2) "양수인", "이관지역" 두 컬럼 기본 숨김
3) 조회/초기화 옆 "양수인/이관지역 보기" 버튼 추가
4) 버튼 클릭 → 두 컬럼 전체 표시 + 문구 "양수인/이관지역 숨기기"
5) 다시 클릭 → 두 컬럼 전체 숨김
6) 검색/수정/삭제/페이지네이션/기존 상세정보 로직은 변경하지 않음
7) DB/백엔드/API 수정 없음

실행
1) ZIP 압축 해제
2) APPLY_AND_PUSH.ps1 실행

PowerShell에서 직접 실행:
powershell -ExecutionPolicy Bypass -File ".\APPLY_AND_PUSH.ps1"

기본 저장소 경로
C:\Users\PC\Documents\GitHub\member-management

안전장치
- main 브랜치가 아니면 중단
- app/static/app.js에 미커밋 변경이 있으면 중단
- git pull --ff-only 실패 시 중단
- 실제 renderClosures 앵커가 예상과 다르면 수정 전 중단
- 수정 전 app.js 백업 생성
- Node.js가 있으면 node --check 통과 후에만 commit/push
- git diff --check 통과 후에만 commit/push
- app/static/app.js만 stage/commit

커밋 메시지
폐업현황: 양수인/이관지역 토글 및 폐업사유 표시

주의
- 과거 대화에 노출된 GitHub PAT는 사용하지 않습니다.
- PC에 이미 설정된 기존 Git 인증을 사용합니다.
