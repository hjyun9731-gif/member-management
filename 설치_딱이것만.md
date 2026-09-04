# 자격증명 발급대장 — GitHub 추가 파일

이 묶음은 **자격증명 발급대장만 추가**합니다. 회원관리·수납/미수금·인허가/변경·보고/집계·기한관리·엑셀 업로드·명단추출의 기존 화면과 API는 삭제하거나 교체하지 않습니다.

## 적용

1. ZIP을 풀어 기존 `member-management` 저장소 최상단에 파일을 합칩니다.
2. 저장소 최상단에서 아래 명령을 한 번 실행합니다.

   ```bash
   python install_certificate_ledger.py
   ```

3. 아래 명령으로 변경 범위를 확인합니다.

   ```bash
   git diff -- app/main.py app/routers/candidates.py app/static/index.html
   ```

4. GitHub에 전체 변경을 커밋·푸시합니다. Railway가 연결돼 있으면 자동 배포됩니다.

## 기존 파일에서 추가되는 연결 코드

- `app/main.py`: 신규 모델 import와 신규 라우터 include
- `app/routers/candidates.py`: 예정자 `등록 완료` 성공 뒤 대장 상태를 `인가완료`로 바꾸는 오류 격리 훅
- `app/static/index.html`: 독립 UI 스크립트 1줄

기존 줄 삭제는 없습니다. 나머지는 전부 신규 파일입니다.

## 동작

- `+ 자격증명 생성`: 예정자 검색·선택, 자격증번호 입력, 증명서 No. 기존 번호 사용 또는 자동 채번
- 처리상태: `인가대기 → 인가완료 → 발급완료`
- 예정자 목록에서 `등록 완료`: 연결된 대장이 자동으로 `인가완료`
- `발급완료`: 발급일·최근 담당자를 기록하고 첨부 원본 디자인의 A6 양식을 엽니다.
- `이력`: 생성자, 인가완료 담당자, 발급완료 담당자와 처리 시간을 확인합니다.
- 신규 DB 테이블: `certificate_issuance_ledger`, `certificate_issuance_history` 두 개만 생성

## 자동 테이블 생성

Railway 시작 시 SQLAlchemy가 신규 테이블만 `CREATE TABLE IF NOT EXISTS` 방식으로 만듭니다. 기존 테이블은 변경하지 않습니다. 자동 생성이 막힌 환경에서만 `migrations/20260904_add_certificate_ledger.sql`을 PostgreSQL에 한 번 실행합니다.
