V35 — [사용]2026미수금.xlsx authoritative baseline + 전체 DB profile 재대조
====================================================================

최종 기준원장
- [사용]2026미수금.xlsx
- SHA-256: 5ea04905d47e6ff16f0b0bd763695618590afa02a8b6852352bbd73e440f820c
- 2026-08-31 baseline

원장 전수검사
- sheet1 데이터행: 3,269행
- 완전공란 source_row 1315: 제외
- 성명공란 source_row 101 / 춘천시 / 차량 5340 / 미수 510,000원: 삭제하지 않고 baseline 보존
- 최종 baseline seed: 3,268행
- 원본↔JSON 직접 대조: 124,184개 필드
- 불일치: 0건
- source_row 중복: 0건
- payment_date ####### 잔존: 0건
- 지역명 (숫자) suffix 잔존: 0건

8월말 baseline 요약
- 미수: 2,650명 / 327,278,000원
- 선납: 193명 / 4,803,000원
- 완납: 425명

대표 검증
- 한인교 2046: 1~8월 매월 부과 5,000 / 입금 5,000 / 월말 0원
- 주신평 7명: 1월·7월 각 30,000원씩, 월 합계 210,000원
- 허장덕 합동2: 6~8월 현재회원 귀속 각 60,000원 확인
- 조철만 합동1: 6~8월 각 110,000원 지정분배 확인
- 정우영 85-2022: 8월말 50,000원

운영 DB 재정합성
1. 운영 DB의 누락 receivable profile을 먼저 생성
2. 운영 DB 전체 profile(약 3.8천명)을 전부 순회
3. authoritative seed를 회원과 1:1 재매칭
4. 하나의 source_row를 두 회원에게 중복귀속 금지
5. 성명공란 5340은 지역+차량 끝4자리 후보가 DB에서 단 1명일 때만 연결
6. matched legacy 회원의 2026-01~08 source='auto' 오부과 삭제
7. 2026-09 이후 정상부과만 다시 생성
8. 실제 수납 / 수동 금액수정 / 연락 / 폐업 데이터는 삭제하지 않음
9. 2026 비배 일반 관리비 금지 / 2027-01 시작 규칙 유지
10. verify API에 total_profiles_checked / matched_profiles / nonlegacy_profiles_after_reconcile /
    unmatched_seed_count / duplicate source_row 상태를 기록

한인교 예상
- 8월말 baseline: 0원
- 9월 정상 70세 부과가 아직 미납이면 현재잔액: 5,000원
- 45,000원으로 나오면 비정상이며, V35는 기존 1~8월 auto 40,000원을 제거하도록 처리함

수정 파일
- app/data/legacy_receivables_2026.json
- app/routers/receivables.py

대시보드 HTML/CSS/JS는 포함하지 않음 — 현재 GitHub main의 UI를 덮어쓰지 않음.

검증 파일
- VERIFY_V35_ALL_MEMBER_AUTHORITATIVE.txt
- V35_BASELINE_ANOMALIES.csv (헤더만 존재 = 불일치 0건)
