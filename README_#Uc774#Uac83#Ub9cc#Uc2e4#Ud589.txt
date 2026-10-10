[사용법]

1. 이 ZIP을 압축 해제
2. PowerShell에서 압축 푼 폴더로 이동
3. 아래 한 줄 실행

powershell -ExecutionPolicy Bypass -File ".\적용하고_GitHub_push.ps1"

그 외에는 할 것 없음.

[자동으로 하는 일]
- 로컬 member-management main 최신 git pull
- 2026-09-16 최종원장 보정 패키지 검증
- app/main.py 최소 패치
- Python 문법검사 + git diff --check
- GitHub main commit/push
- Railway 자동배포 후 운영 health endpoint 확인

[운영 반영]
- 수납/미수금 271건: 2026-09-16 14:01 운영 스냅샷 대비 확정 차액(delta) 1회 반영
- 자격증명 미발급 확정 28명: 부과기준일 없음 유지
- 해당 28명은 sync나 월 자동부과가 다시 돌아도 발급 전까지 DB trigger가 재부과 차단
- 자격증명 발급정보가 회원마스터에 입력되면 다음 달 1일부터 정상 부과로 자동 전환
- 기존 수납/선납/연락/폐업/양도/이관 이력은 삭제하지 않음
- 기존 v1 패치가 이미 실제 반영된 DB라면 271건 delta를 중복 적용하지 않고 보호장치만 업그레이드

[GitHub에 올라가는 파일]
- app/main.py (import/job/health route 최소 추가)
- app/receivables_hotfix_20260916.py
- app/data/receivables_hotfix_20260916.json

원본 Excel/CSV, 주소, 전화번호, 주민등록번호는 GitHub에 올리지 않음.
토큰/비밀번호도 이 패키지에 없음.
