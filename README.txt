RECEIVABLES-CURRENT 비고 오염 긴급 수정

문제:
- [RECEIVABLES-CURRENT-20260906] 같은 내부 표식이 회원 비고에 저장됨.
- 폐업 처리 시 회원 비고가 폐업 비고로 복사되면서 폐업현황에도 퍼짐.

수정:
- 이미 오염된 license_holders / closures / candidates / transfer_ledger 비고에서 내부 표식만 제거
- 사람이 작성한 다른 비고는 그대로 유지
- 앞으로 ORM 저장 직전에 해당 내부 표식을 자동 제거하여 재발 차단

적용:
1) 이 ZIP 내용을 현재 member-management 저장소 루트에 풉니다.
2) 저장소 루트에서:
   python apply_receivables_memo_fix.py
3) 확인:
   git status
4) 커밋/푸시:
   git add app/main.py app/memo_sanitizer.py
   git commit -m "fix receivables memo pollution"
   git push origin main
5) Railway 자동배포 완료 후 새로고침

이미 오염된 DB를 배포 전에 즉시 정리하려면:
migrations/20260909_cleanup_receivables_current_memo.sql
을 Railway PostgreSQL SQL 콘솔에서 실행합니다.

예:
'협회 명단에 없음. [RECEIVABLES-CURRENT-20260906]'
→ '협회 명단에 없음.'

'[RECEIVABLES-CURRENT-20260906]'
→ 빈 비고
