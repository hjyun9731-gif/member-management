# 자격증명발급번호 중복 방지 / 발급대장 속도 개선

## 중복 방지
- 같은 번호 판단: `YY-N` 표준형 (26-085 == 26-85 == 2026-0085 == 26-85(운전자명)). 규칙은 `app/services/certificate_number_rules.py`.
- 검사 대상 5곳: 예정자, 회원(개인/택배), 양도양수대장, 폐업현황, 자격증명발급대장. `app/services/certificate_number_guard.py`.
- 서버 차단(409, "이미 등록된 자격증명발급번호입니다. (번호 → 기존 대상)"): 예정자 등록/수정, 회원 등록/수정, license-holders 등록/수정, 양도양수 등록/수정, 도내 양도양수, 발급대장 번호수정.
- 동시 등록: PostgreSQL `pg_advisory_xact_lock('certnum:<번호>')`를 확인~저장 구간에 잡는다(저장 commit까지 유지).
- 같은 사람의 연결 기록(예정자→회원 전환, 양도양수 이월)은 중복으로 보지 않는다.
- 화면: 입력 즉시 `GET /api/certificate-ledger/number-check` 로 확인해 경고창+인라인 문구 표시.
- 기존 중복 목록(조회 전용, 관리자): `GET /api/certificate-ledger/number-duplicates`. 데이터를 수정/삭제하지 않는다.
- 미적용: 엑셀 일괄 업로드, 폐업현황 저장(기존 회원 번호를 그대로 옮기는 화면).

## 속도
- 목록: 필터/검색이 없으면 (id, 번호)만 읽어 정렬·페이징하고 현재 페이지만 상세 계산.
- 대상 없는 번호 로그 보강: 매 조회 → 60초 TTL.
- 스키마 점검: 요청마다 → 프로세스당 1회.
- `/candidates`: 전체 발급대장 로딩 → 반환된 예정자 id만.
- 측정: `scripts/bench_certificate_ledger.py` (배포 전/후 실행).
