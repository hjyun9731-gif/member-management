# DQ 진단 / PR #13 통합 검증 (2026-11-08)

## 결과

| 검증 | 최종 결과 |
| --- | --- |
| 기존 PR #13 SQLite 기준선 | 277 passed, 1 skipped (25.32s) |
| 최종 SQLite 전체 회귀 | 297 passed, 2 skipped (24.94s) |
| 최종 PostgreSQL 17.11 전체 회귀 | 299 passed (49.29s) |
| 실제 Chromium 156 / SQLite | 브라우저 검증 8항목 통과, pageerror 0 |
| 실제 Chromium 156 / PostgreSQL | 브라우저 검증 8항목 통과, pageerror 0 |
| JS 문법 / diff 공백 검사 | 통과 |
| 새 CI 워크플로 YAML 검사 | 통과, 실제 GitHub CI 실행은 미수행 |

SQLite의 skip 2건은 PostgreSQL 전용 원장 감사 및 동시 등록 잠금 검증이다.
기존 의존성의 경고 141건(주로 SQLAlchemy Query.get / datetime.utcnow / FastAPI on_event 폐기 예정 경고)은 남아 있다.

## 최신 코드 비교

확인한 원격 참조:

- main: `242ce7ab814a3bbc73b8641aac6fba7c00074e57`
- PR #13 / `review/integrity-verified-20261107`: `fffbfcbf140f6ef1f197440dca34bcd34817ecda`
- `review/data-integrity-diagnostics`: `75e6894571549f1c87b26607495fb1511f379d65`

DQ 브랜치 커밋은 이미 PR #13의 조상이다(`git merge-base --is-ancestor` 통과).
따라서 재병합/cherry-pick 없이 PR #13 커밋에서 검토 브랜치를 만들고 누락된 통합 동작만 보완했다.
PR #13의 중복 회원 방지, 자격증명 동기화 원자성, 전환 예정자 재연결 후보 제외, 연락처 분리 편집은 유지했다.

## 수정한 동작

1. **관리번호 진단과 등록 차단 기준 통일**: `양26-0100`, 내부 공백, 전각 숫자, 끝의 마침표/쉼표/가운뎃점/세미콜론 등을 비교 시 동일하게 처리한다. 저장된 원문은 바꾸지 않는다. 회원·예정자·양도양수·폐업의 점유 진단도 같은 기준으로 조회한다.
2. **CSV와 화면 필터 일치**: 유형·판정 외에도 개인/택배·지역·상태·성명·관리번호·차량번호를 CSV 내보내기에 전달한다. 진단 화면에 지역/상태 필터를 추가하고 연락처 진단 결과의 상태 누락을 수정했다.
3. **애매한 대리인 자동 연결 차단**: 같은 회원의 현재 대리인 이력이 여러 건이면, 전화번호 일치가 1건이어도 회원 미러 필드를 자동 변경하지 않는다. 기존 이력은 유지한다.
4. **전체 회귀의 양 DB 지원**: 기본 실행은 매번 임시 SQLite를 사용한다. PostgreSQL은 명시적으로 지정한 loopback `mm_test_*` DB만 허용하며, 독립 서비스 테스트마다 별도 임시 스키마를 사용한다. 기존 테스트가 남긴 세션의 읽기 잠금과 고정 ID 시드의 PostgreSQL 시퀀스 차이를 정리했다.
5. **CI 추가**: PR / review 브랜치의 SQLite·PostgreSQL 전체 회귀를 수행하는 읽기 권한 워크플로를 추가했다. 배포 단계는 없다.

## 검증 범위

- DQ-01~15를 모두 발생시키는 합성 데이터로 판정을 검사했다.
- 진단 실행 중 DML/DDL SQL 부재를 검사하고, PostgreSQL에서는 실제 READ ONLY 트랜잭션 안에서 진단을 수행했다.
- 두 스레드가 같은 예정자를 동시에 회원으로 전환할 때 성공 1건 / 차단 1건 / 회원 1건을 확인했다.
- 기존 전체 테스트로 중복 등록 차단, 동명이인/전화 공유 허용, 양도양수·폐업 연결, 발급번호 표기 차이, 전환 예정자 후보 제외, 잘못된 재연결 차단, 회원 저장 동기화 실패 시 전체 롤백, 발급완료 상태 보존, 대리인 이력·전화 검색, 변경이력·수납/미수금·통장 업로드를 재검증했다.
- 실제 브라우저에서는 로그인, DQ 15종 UI, 지역/상태/성명/유형 필터와 동일한 CSV, 관리번호 표기 차이 점유 진단, 재연결 미리보기/후보 조회 및 쓰기 기본 차단, 전화번호 검색, 복수 연락처 수정 폼, 주소 저장·재조회·발급완료 이력 보존을 확인했다.
- 브라우저에서 진단/미리보기/내보내기만 수행한 전후 **모든 ORM 테이블의 전체 행**을 비교해 불변임을 확인했다. 주소 수정은 별도의 합성 회원을 대상으로 수행했다.

## 재현

```bash
python -m pip install -r requirements.txt 'pytest>=8,<10' playwright
python -m pytest -q
```

PostgreSQL은 새로 만든 폐기 가능한 로컬 DB에서만 실행한다. 아래 예시의 계정·포트는 해당 테스트 서버에 맞춘다.
`DATABASE_URL`만 지정하면 테스트는 이를 무시하고 임시 SQLite로 실행하므로 PostgreSQL 실행에는 별도 변수가 필요하다.

```bash
MM_TEST_POSTGRES_URL=postgresql://postgres:postgres@127.0.0.1:5432/mm_test_ci python -m pytest -q
python -m playwright install chromium
MM_CHROMIUM_EXECUTABLE=/path/to/chrome python tests/browser_integrity_smoke.py --out /tmp/mm-browser-sqlite
MM_TEST_POSTGRES_URL=postgresql://postgres:postgres@127.0.0.1:5432/mm_test_browser MM_CHROMIUM_EXECUTABLE=/path/to/chrome python tests/browser_integrity_smoke.py --out /tmp/mm-browser-pg
```

브라우저 PostgreSQL 실행에는 회귀 테스트 DB와 다른 **비어 있는** `mm_test_browser` DB를 사용한다.
이 작업 환경에서는 일반 Chromium이 UNIX 소켓 제한으로 실행되지 않아 Debian 공식 headless-shell과
`MM_CHROMIUM_ARGS='--single-process --no-zygote --in-process-gpu --disable-gpu'`를 사용했다.
연결된 외부 브라우저는 로컬 임시 서버에 접근할 수 없어 별도 로컬 Chromium으로 검증했다.
테스트 서버는 startup 백그라운드 유지보수를 끈 상태(`--lifespan off`)에서 실제 API와 데이터베이스를 사용한다.
운영 데이터/운영 스키마 크기에서의 성능 및 Railway 배포 후 검증은 수행하지 않았다.

## 증거

`docs/validation/20261108/{sqlite,postgresql}/`에 JUnit 전체 결과, 브라우저 결과 JSON,
진단 필터 화면 및 저장 후 재조회 화면을 첨부했다. 모든 화면 데이터는 합성 자료다.

## 운영 보호 및 원격 게시 상태

- main 병합, Railway 배포, 운영 DB 접속/수정 없음.
- 정정 실행 환경변수 및 운영 설정 변경 없음. 테스트에서는 정정 실행 차단을 확인했다.
- 기존 Railway 워크플로는 main push/수동 실행에만 반응한다. 해당 워크플로는 변경하지 않았다.
- GitHub PR #13이 Open이고 head가 fffbfcb임을 공개 페이지에서 확인했다.
- `git push -u origin HEAD:refs/heads/review/integrity-dq-integration-20261108`는 GitHub Username 인증 정보가 없어 실패했다(terminal prompts disabled). `gh pr create`도 GitHub CLI 미로그인으로 실패했다. 원격 검토 브랜치와 새 PR은 생성되지 않았다. GitHub 쓰기 인증 연결 후 아래 명령으로 게시할 수 있다.

```bash
git push -u origin review/integrity-dq-integration-20261108
gh pr create --repo hjyun9731-gif/member-management --base review/integrity-verified-20261107 --head review/integrity-dq-integration-20261108 --title "Integrate DQ diagnostics with PR #13 safety fixes" --body-file docs/PR-integrity-dq-integration.md
```
