[2026-10-07 수납·미수금 V2 — 지로희망 표시 + 종료자 폐업관리]

이 파일은 이전 V1 패치가 화면에 반영되지 않은 경우를 위한 교체본입니다.
GitHub main에서 아래 7개 파일만 같은 경로로 업로드/덮어쓰기 하세요.

1. app/database.py
2. app/railway_entry.py
3. app/routers/receivables_patch_20261007_v2.py
4. app/data/receivables_patch_20261007.json
5. app/static/receivables.html
6. app/static/receivables.js
7. app/static/receivables.css

핵심 변경
- database.py: Railway PostgreSQL URL을 현재 설치된 psycopg2 드라이버로 정규화합니다.
- 지로희망: DB 플래그 테이블에 의존하지 않고 원장 명단(성명+차량번호)으로 바로 배지를 표시합니다.
- 종료자: 성명+차량번호 정확일치만 처리합니다.
- 종료자 1건이 오류나도 정상 처리된 다른 건은 롤백하지 않습니다.
- 종료자는 활성회원에서 제외되고 폐업관리 이력을 생성/연결합니다.
- 종료월 다음 달 이후 source=auto 자동부과만 삭제합니다.
- 기존 입금/연락/V4 보정/수동 잔액은 수정하지 않습니다.

배포 후 확인
- Railway 로그에 "receivables 20261007 V2 patch:" 문구가 나오는지 확인
- /receivables에서 Ctrl+F5 강력 새로고침
- 지로희망 대상자의 이름 옆에 노란 "지로희망" 배지 확인
- 폐업관리에서 종료자 확인
- 종료자가 활성 수납목록에서 빠졌는지 확인

상태 확인 API(로그인 상태에서 사용)
GET /api/receivables/patch-20261007-v2/status

수동 재적용(관리자 토큰 필요)
POST /api/receivables/patch-20261007-v2/apply
