2026-10-08 수납·미수금 UI 긴급복구

증상: 화면이 회색으로 깨지거나 엉뚱한 정적 페이지가 표시됨.

GitHub main에 아래 4개 파일을 정확한 경로로 덮어쓰기:
- app/static/receivables.html
- app/static/receivables.js
- app/static/receivables.css
- app/static/sw.js

이 패치는 DB/수납/미수금 데이터를 수정하지 않습니다. UI 정적파일과 캐시만 복구합니다.
배포 후 /receivables를 새로 열면 로그에서 다음 버전을 요청해야 정상입니다:
- /static/receivables.css?v=20261008-ui-recovery-v1
- /static/receivables.js?v=20261008-ui-recovery-v1

기존 20261007-giro-closure-v2가 계속 보이면 GitHub 경로에 파일이 실제로 덮어써지지 않은 것입니다.
