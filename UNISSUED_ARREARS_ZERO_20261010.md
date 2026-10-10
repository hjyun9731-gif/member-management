# 미발급자 관리비 미수금 0원 처리 (2026-10-10)

## 적용 기준
- license_holders.certificate_issue_date와 certificate_number가 **모두 NULL/공백**인 회원
- receivable_profiles.account_type == 관리비인 프로필만
- 엑셀형 원장 기준 현재 관리비 미수금이 **양수**이면, 잔액이 정확히 0원이 되도록 `receivable_payments`에 `method=잔액수정` 기록을 생성
- 기존 실제 입금/과거 부과/기타 수수료 이력 삭제하지 않음
- 선납(음수) 금액 유지
- 신규 자동부과는 기존 `_auto_charge_allowed_for_month()`에서 미발급자에게 차단

## 실행 방법 (운영 DB에 적용 전 백업 확보 필수)

1. 먼저 **새 배포본 코드를 테스트 환경에서 검증**한다. `main` 브랜치 직접 덮어쓰기 금지.
2. Railway 환경과 동일한 의존성/DB 스키마를 테스트한다.
3. **현재 DB 백업**을 확보한다.
4. 일회성 미리보기: `python -m scripts.zero_unissued_management_arrears` (DB 수정 없음)
5. 대상·현재 잔액·예상 조정액 확인 후 실행 승인
6. 일회성 실제 처리: `python -m scripts.zero_unissued_management_arrears --apply --confirm ZERO_UNISSUED_MANAGEMENT`
7. 화면 및 정정 이력에서 실제 잔액 0원 확인

## 주의
- 이 파일은 DB에 직접 적용된 상태가 아닙니다.
- 엑셀형 원장 계산식(9월말 기준잔액 + 10월 이후 부과 − 10월 이후 수납)을 기준으로 정정합니다.
- 다른 기존 관리 화면이 별도 과거 부과를 합산하는 경우 표시 잔액이 다를 수 있어 통합 검증이 필요합니다.
- 이번 변경은 별도 `pg_dump` 백업을 생성하지 않습니다. 백업 없이 --apply 금지.
