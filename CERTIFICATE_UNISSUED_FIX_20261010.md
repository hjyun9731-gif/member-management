# 자격증명 미발급 관리비 차단 — 2026-10-10

## 업무 규칙
- `license_holders.certificate_issue_date`와 `license_holders.certificate_number`가 **둘 다 NULL/공백**인 회원은 자격증명 미발급자로 판정.
- 계정종류가 **관리비**이면 해당 회원의 월 자동부과를 생성하지 않음. 2026년/2027년 구분 없이 적용.
- 한 필드만 존재하는 회원은 자동으로 미발급으로 판정하지 않음(예: 발급번호 있는 송제욱).
- 기존 자동부과 기록 및 기준 잔액은 자동 삭제·차감하지 않음. 별도 장부 대조/승인이 필요한 정정 대상.
- 협회비는 이 규칙에 의해 차단되지 않음.

## 수정 파일
- `app/routers/receivables.py`: 누락 판정 및 새 관리비 부과 차단, 기존 부과 자동삭제 방지
- `app/routers/receivables_workspace.py`: 관리자 읽기전용 점검 SQL에서 발급일+발급번호를 함께 검사
- `tests/test_certificate_unissued_guard_pure.py`: 7개 로직 단위 테스트

## 확인 결과
- 단위 테스트 7개 통과 (`pytest -q tests/test_certificate_unissued_guard_pure.py`)
- Python 문법검사 통과
- Railway 실제 PostgreSQL DB에 직접 접속한 검증/데이터 수정은 하지 않음

## 미완료 — 운영 DB 변경 전 별도 확인 필요
- 김종진/허정호 기존 2026-10 자동부과 각 5,000원 정정
- 27명 2026-09 총 135,000원의 `legacy_balance` 반영 여부 검토
- 백업 및 실제 PostgreSQL 통합 테스트
- `main` 배포 및 기존 DB 트리거/배치와 실제 통합 검증
