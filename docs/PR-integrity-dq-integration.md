# DQ 진단과 PR #13의 등록·재연결 안전장치 통합

관리번호 표기 차이로 등록이 차단되는데 점유 진단에서는 대상이 누락되고, 화면 필터와 CSV 결과가 달라지는 문제를 수정합니다. 현재 대리인 이력이 여러 명이면 전화번호가 한 명과만 일치해도 자동 연결을 중단합니다.

DQ-01~15 브랜치는 이미 PR #13의 조상이므로 `fffbfcb`에서 필요한 보완만 추가했습니다. 기존 등록 중복 차단, 자격증명 재연결, 연락처 분리 편집, 회원 저장 실패 시 전체 롤백을 전체 회귀로 확인했습니다.

검증:

- SQLite: 297 passed, 2 skipped (PostgreSQL 전용 테스트).
- PostgreSQL 17.11: 299 passed. 같은 예정자 동시 전환 시 회원 1건 생성 확인.
- 실제 Chromium: SQLite/PostgreSQL 각각 8항목 통과, 페이지 JS 오류 0.
- 진단·미리보기·CSV 조회 전후 전체 테이블 불변 확인. PostgreSQL READ ONLY 트랜잭션 검사 통과.
- PR/review 브랜치용 양 DB 회귀 CI 추가. GitHub CI는 아직 실행하지 않음.

자세한 범위·재현 명령·JUnit·화면 증거: `docs/integrity-integration-20261108.md`, `docs/validation/20261108/`.

main 병합, Railway 배포, 운영 DB 접속·수정, 정정 실행 활성화는 하지 않았습니다.
이 PR의 권장 base는 `review/integrity-verified-20261107`이며 PR #13 위에 쌓는 추가 검토용 변경입니다.
