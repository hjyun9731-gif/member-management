# member-management 수납/미수금 통합 모듈

대상 저장소: `hjyun9731-gif/member-management` / `main` / Railway production

## 이 패치가 지키는 원칙

기존 회원관리의 **회원등록, 회원수정, 폐업, 양도양수, 자격증명, 월마감, 엑셀업로드, 기존 API/DB 컬럼을 변경하지 않습니다.** 새로운 `receivable_*` 테이블과 라우터/화면만 추가합니다.

미수금 모듈은 기존 `license_holders`를 회원마스터로 직접 읽기 때문에:

- 전체회원관리에서 신규등록 → 미수금에서 자동 인식
- 신규등록월에는 부과 없음 → **다음 달 1일부터 첫 부과**
- `membership_status == 가입` → 신규회원 협회비 기본값 10,000원
- 그 외 신규회원 → 관리비 기본값 5,000원
- 기존 2026 장부의 `협회비 / 관리비 / 70세` 계정은 원본 그대로 이관
- 전체회원관리에서 폐업/양도/이관 → 활성 수납대상에서 자동 제외
- 폐업 전에 남은 미수금은 **폐업 미수금**에서 계속 표시·수납 가능
- 연락상태: 미연락 / 연락완료 / 부재 / 재연락 필요 / 문자발송 + 날짜/방법/담당자/메모 이력 저장

## 설치

저장소를 백업/브랜치 분기한 뒤, 이 ZIP을 풀고:

```bash
python install_receivables.py --repo /path/to/member-management
```

기존 메인 화면에 바로가기 버튼도 추가됩니다. 버튼 추가조차 원치 않으면:

```bash
python install_receivables.py --repo /path/to/member-management --no-launcher
```

설치 후 URL:

```text
/receivables
```

예: `https://member-management-production.up.railway.app/receivables`

## DB 추가 테이블

- `receivable_profiles`
- `receivable_charges`
- `receivable_payments`
- `receivable_contact_logs`

기존 테이블은 DROP/ALTER하지 않습니다. `Base.metadata.create_all()`이 새 테이블만 생성합니다.

## 기존 엑셀 이관

`app/data/legacy_receivables_2026.json`에 원본 `[사용]2026미수금`의 3,239명, 1~12월 부과/입금/입금일/미수금, 계정, 이월금 기반 데이터를 포함했습니다. 프로그램은 기존 `license_holders`와 **차량번호+성명 우선 매칭**합니다.

2026년 기존 장부 마지막 데이터 다음 달부터 프로그램 자동부과가 이어집니다. 신규회원은 인가일자 기준 다음달 1일부터 시작합니다.

## 반드시 배포 전 확인

1. 운영 DB 백업
2. 별도 작업 브랜치에서 설치
3. 기존 신규등록/폐업/회원수정 등 회귀 테스트
4. `/receivables`에서 회원 동기화
5. 테스트 신규회원: 9/1 등록 → 10/1 첫 부과 확인
6. 테스트 폐업회원: 기존 미수 유지 + 폐업 미수금 화면 표시 확인
7. 입금/연락 기록 확인
8. 이상 없을 때 main 반영

## 롤백

코드 연결만 제거:

```bash
python uninstall_receivables.py --repo /path/to/member-management
```

DB의 `receivable_*` 테이블은 데이터 보존을 위해 자동 삭제하지 않습니다.
