member-management 수납·미수금 UI 가독성 수정본
기준: member_management_FINAL_ALL_REQUIREMENTS_20260829.zip
수정일: 2026-08-29

[수정 내용]
1. 수납처리 / 미수금현황 / 연락관리 목록
- 지역을 주소와 분리해 독립 컬럼으로 표시
- 핸드폰 번호 전체 표시
- U+ 문자는 유지하되 전화기/통화 아이콘 및 tel: 링크 없음
- 첫 부과일을 목록에 독립 컬럼으로 추가
- 부과상태와 첫 부과일을 분리해 가독성 개선
- 왼쪽 목록 영역 폭 확대, 오른쪽 빈 공간 과다 문제 완화

2. 폐업관리
- 지역을 회원명 밑 작은 글씨가 아닌 독립 컬럼으로 표시
- 핸드폰 번호 독립 컬럼 추가
- 연결된 폐업회원은 목록에서 바로 U+ 문자 가능
- 처리정보를 '지역 → 이관/양도지역' + 접수일/처리일/양수인으로 정리
- 처리정보 줄바꿈 허용, 말줄임 제거
- 현재 폐업자 / 전체 폐업이력 모두 적용
- 폐업 API 응답에 Closure의 mobile/phone을 보강하고 폐업 검색에서도 핸드폰/전화번호 검색 가능

3. 상세 화면
- U+ 문자 버튼 유지
- 핸드폰번호가 없는 회원은 U+ 문자 버튼 비활성화

[변경 파일]
- app/routers/receivables.py
- app/static/receivables.html
- app/static/receivables.js
- app/static/receivables.css

[검증]
- python -m py_compile app/routers/receivables.py : PASS
- node --check app/static/receivables.js : PASS
- static/ 및 receivables.py에서 tel:, 전화기 아이콘(☎/📞) 없음 확인

[Git 명령]
git status
git add app/routers/receivables.py app/static/receivables.html app/static/receivables.js app/static/receivables.css
git commit -m "수납 폐업관리 가독성 및 U+ 문자 UI 수정"
git push origin main
