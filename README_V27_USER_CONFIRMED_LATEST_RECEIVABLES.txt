member-management 수납·미수금 V27
기준일: 2026-09-02

사용자 최종 지정 미수금 기준원장:
- 2026미수금_1-8월_수식서식보존_최종(1).xlsx

이전 V26에서 잘못 기재된 기준원장명을 폐기하고, 위 파일을 최신 authoritative source로 고정했습니다.
새 baseline source_sha256 버전으로 갱신되어 Railway DB의 2026-08 snapshot이 1회 refresh됩니다.

유지 사항:
- data_through_month = 8
- 기존 입금 이중차감 방지 cutover 유지
- 2026년 비(非)배 일반 관리비 자동부과 금지
- 비배 일반 관리비는 2027-01부터
- 배 번호 관리비 2026 부과 규칙 유지
- 실제 음수 잔액만 선납
- UI V23/V26 유지

GitHub 저장소 루트에 압축을 풀어 덮어쓴 뒤 commit/push 하세요.
