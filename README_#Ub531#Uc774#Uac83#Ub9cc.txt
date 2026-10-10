[해야 할 것]

1) 이 ZIP을 아무 폴더에 압축 해제
2) PowerShell에서 압축 푼 폴더로 이동
3) 아래 한 줄 실행

powershell -ExecutionPolicy Bypass -File ".\적용하고_GitHub_push.ps1"

끝.

이 스크립트가 자동으로:
- C:\Users\PC\Documents\GitHub\member-management 에 파일 2개 추가
- app/main.py에 실행 줄 2곳만 최소 추가
- 문법검사
- git add
- git commit
- git push origin main
까지 수행합니다.

GitHub main push 후 Railway는 기존 연결대로 자동배포됩니다.

[운영 반영 내용]
- 총 271건 잔액 차액 보정
- 자격증명 미발급 28명: first_charge_date NULL + 부과기준일 없음 처리
- 기존 입금/연락/폐업/양도 이력 삭제 안 함
- 2026-09-16 14:01 이후 운영에서 새로 발생한 입금/수정은 보존하기 위해 목표금액 강제 덮어쓰기가 아니라 delta 방식으로 적용
- receivable_system_state에 patch id 저장 -> Railway 재시작되어도 중복 적용 안 됨

[GitHub에 엑셀 원본은 안 올라감]
개인정보가 들어 있는 최종 원본 XLSX는 커밋하지 않습니다.
GitHub에는 보정코드와 필요한 최소 보정값만 올라갑니다.
