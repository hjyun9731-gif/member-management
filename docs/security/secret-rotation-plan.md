# 노출된 비밀정보 교체 계획 (실행 전 승인 필요 — 이 문서는 계획일 뿐 아무것도 변경하지 않습니다)

## 노출 범위 (값은 기록하지 않음)
- 공개 저장소 과거 커밋 `5b9ffb8`(2026-05-03)의 `.env`: 주석 처리된 원격 `DATABASE_URL`(계정·비밀번호 포함) 1개, `SECRET_KEY`(예시값 아님).
- 현재 `main`에는 `.env`가 추적되지 않음(2026-08-27 `70dedb3`에서 제거). 다만 과거 커밋에는 영구히 남아 있음.
- 채팅에 평문으로 공유된 GitHub 토큰 3개(이전 채팅 1개, 이번 대화 2개) → 사용 여부와 관계없이 폐기 대상.

## 순서 (업무시간 외, 사용자가 직접 수행)
1. **GitHub 토큰 폐기**: GitHub → Settings → Developer settings → Personal access tokens → 해당 토큰 Delete. 필요하면 최소 권한(해당 저장소, Contents: Read/Write) 토큰을 새로 만들고 채팅에 붙여넣지 않는다.
2. **백업**: Railway → Postgres 서비스 → Backups 에서 백업 생성(복구 가능 여부 확인).
3. **SECRET_KEY 교체**: 새 값 생성 `python -c "import secrets;print(secrets.token_urlsafe(48))"` → Railway 앱 서비스(gallant-joy) Variables 의 `SECRET_KEY` 교체 → 재배포. 전원 로그아웃됨(재로그인 필요).
4. **DB 비밀번호 교체**: (a) 먼저 DB에서 `ALTER USER <계정> WITH PASSWORD '<새 비밀번호>';` (b) 즉시 Postgres 서비스의 비밀번호 변수와 앱 서비스의 `DATABASE_URL`(참조 변수 `${{Postgres...}}` 사용 여부 확인)을 새 값으로 맞춤 → 앱 재배포. 사이에 짧은 중단 가능.
5. **과거 노출 DB가 운영 DB와 다른 경우**(예: 이전 Supabase/개발 DB): 그 DB의 비밀번호도 각각 교체하거나 DB를 폐기.
6. **저장소 정리(선택)**: 저장소를 Private 으로 전환(가장 간단). 기록 삭제(git filter-repo + force push)는 모든 클론/브랜치(4개)에 영향 → 별도 승인 후, 비밀번호 교체가 끝난 뒤에만.
7. **재발 방지**: `.gitignore`에 `.env` 유지, GitHub → Settings → Code security → Secret scanning / Push protection 켜기.

## 확인(교체 후)
- 로그인 정상, 수납·미수금 메뉴 재로그인 없음, Railway 로그에 DB 인증 오류 없음, 구 비밀번호로 접속 시 거부됨.
