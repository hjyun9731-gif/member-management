-- =====================================================================
-- 수납·미수금 V4 읽기 전용 감사 SQL  (SELECT 전용 — INSERT/UPDATE/DELETE 없음)
-- 규칙: 한 번에 "한 개 쿼리"씩 선택해서 실행하세요. 결과의 이름/차량번호는 마스킹되어 있습니다.
-- 기준일: 9월말 확정(2026-09-30). 날짜 컬럼은 문자열(YYYY-MM-DD)입니다.
-- =====================================================================

-- [Q1] V4(및 이전 보정)가 실제로 적용되었는가?
SELECT key,
       value::json->>'status'     AS status,
       value::json->>'applied_at' AS applied_at,
       updated_at
FROM receivable_system_state
WHERE key LIKE 'receivables_reconcile_20261008%' OR key LIKE 'legacy_baseline%'
ORDER BY key;

-- [Q2] V4가 취소 처리한 수납내역 (건수·금액)
SELECT COALESCE(cancelled_by,'(취소자 없음)') AS cancelled_by,
       COUNT(*) AS 건수, COALESCE(SUM(amount),0) AS 금액합계,
       MIN(payment_date) AS 최초일자, MAX(payment_date) AS 최종일자
FROM receivable_payments
WHERE cancelled_at IS NOT NULL
GROUP BY cancelled_by
ORDER BY 건수 DESC;

-- [Q3] 9/30 이전 수납 중 "취소되지 않은" 건 (V4 이후엔 0이어야 정상)
--      V4 적용 회원 기준. 0이 아니면 이중 차감 후보입니다.
SELECT COUNT(*) AS 건수, COUNT(DISTINCT p.member_id) AS 회원수, COALESCE(SUM(p.amount),0) AS 금액합계
FROM receivable_payments p
JOIN receivable_profiles r ON r.member_id = p.member_id
WHERE p.cancelled_at IS NULL
  AND p.payment_date <= '2026-09-30'
  AND r.legacy_note LIKE '%[20261008 월별장부 전수정정 V4]%';

-- [Q3-상세] 위 후보 회원 (최대 50명, 마스킹)
SELECT p.member_id,
       left(l.name,1)||repeat('*',greatest(char_length(l.name)-1,0)) AS 이름,
       '…'||right(l.vehicle_number,4) AS 차량,
       COUNT(*) AS 건수, SUM(p.amount) AS 금액, MIN(p.payment_date) AS 최초, MAX(p.payment_date) AS 최종,
       r.legacy_balance AS 확정잔액
FROM receivable_payments p
JOIN receivable_profiles r ON r.member_id = p.member_id
JOIN license_holders l ON l.id = p.member_id
WHERE p.cancelled_at IS NULL AND p.payment_date <= '2026-09-30'
  AND r.legacy_note LIKE '%[20261008 월별장부 전수정정 V4]%'
GROUP BY p.member_id, l.name, l.vehicle_number, r.legacy_balance
ORDER BY SUM(p.amount) DESC LIMIT 50;

-- [Q4] V4 적용(표식) 회원 수와 9월말 확정잔액 합계
SELECT COUNT(*) AS 적용회원수,
       COALESCE(SUM(legacy_balance),0) AS 확정잔액합계,
       SUM(CASE WHEN legacy_balance < 0 THEN 1 ELSE 0 END) AS 음수잔액회원수
FROM receivable_profiles
WHERE legacy_note LIKE '%[20261008 월별장부 전수정정 V4]%';

-- [Q5] 현재 미수금 재계산 (V4 공식: 확정잔액 + 10월이후 부과 − 9/30이후 유효 수납) 계정별 합계
WITH ch AS (SELECT member_id, SUM(amount) a FROM receivable_charges WHERE billing_month > '2026-09' GROUP BY member_id),
     py AS (SELECT member_id, SUM(amount) a FROM receivable_payments
            WHERE cancelled_at IS NULL AND payment_date > '2026-09-30' GROUP BY member_id)
SELECT r.account_type, COUNT(*) AS 회원수,
       SUM(r.legacy_balance + COALESCE(ch.a,0) - COALESCE(py.a,0)) AS 현재미수금합계
FROM receivable_profiles r
LEFT JOIN ch ON ch.member_id=r.member_id LEFT JOIN py ON py.member_id=r.member_id
WHERE r.receivable_active = 1
GROUP BY r.account_type ORDER BY r.account_type;

-- [Q6] 취소된 수납이 있는데 확정잔액이 0 또는 음수인 회원 수 (수납 반영 확인 후보)
SELECT COUNT(DISTINCT p.member_id) AS 회원수, COALESCE(SUM(p.amount),0) AS 취소금액합계
FROM receivable_payments p
JOIN receivable_profiles r ON r.member_id=p.member_id
WHERE p.cancelled_by LIKE '20261008-monthly%' AND r.legacy_balance <= 0;

-- [Q7] V4 이후 새로 입력된 "9/30 이전 날짜" 수납 (소급 입력 → 이중 차감 위험)
SELECT p.id, p.member_id, p.payment_date, p.amount, p.method, p.created_at, p.created_by
FROM receivable_payments p
WHERE p.cancelled_at IS NULL AND p.payment_date <= '2026-09-30'
  AND p.created_at > (SELECT updated_at FROM receivable_system_state
                      WHERE key='receivables_reconcile_20261008_monthly_v4')
ORDER BY p.created_at DESC LIMIT 50;

-- [Q8] 자격증명 미발급 택배회원(차량번호에 '배')에게 부과된 관리비 — 건수·금액
SELECT c.billing_month, COUNT(DISTINCT c.member_id) AS 회원수, SUM(c.amount) AS 부과금액
FROM receivable_charges c
JOIN license_holders l ON l.id=c.member_id
WHERE l.deleted_at IS NULL AND l.status='active'
  AND (l.category='택배' OR l.vehicle_number LIKE '%배%')
  AND (l.certificate_issue_date IS NULL OR btrim(l.certificate_issue_date)='')
  AND c.amount > 0
GROUP BY c.billing_month ORDER BY c.billing_month;

-- [Q8-상세] 위 회원 목록(최대 100, 마스킹) — 확정잔액·최초부과일 포함
SELECT l.id AS member_id,
       left(l.name,1)||repeat('*',greatest(char_length(l.name)-1,0)) AS 이름,
       '…'||right(l.vehicle_number,4) AS 차량,
       l.approval_date AS 승인일, r.first_charge_date AS 첫부과일, r.account_type AS 계정,
       r.legacy_balance AS 확정잔액,
       (SELECT SUM(amount) FROM receivable_charges c WHERE c.member_id=l.id AND c.amount>0) AS 부과합계,
       (SELECT SUM(amount) FROM receivable_payments p WHERE p.member_id=l.id AND p.cancelled_at IS NULL) AS 유효수납합계
FROM license_holders l
JOIN receivable_profiles r ON r.member_id=l.id
WHERE l.deleted_at IS NULL AND l.status='active'
  AND (l.category='택배' OR l.vehicle_number LIKE '%배%')
  AND (l.certificate_issue_date IS NULL OR btrim(l.certificate_issue_date)='')
  AND EXISTS (SELECT 1 FROM receivable_charges c WHERE c.member_id=l.id AND c.amount>0)
ORDER BY 부과합계 DESC NULLS LAST LIMIT 100;

-- [Q9] 폐업·양도 회원인데 폐업월 "이후" 월에 부과가 남아 있는 경우
SELECT l.id AS member_id,
       left(l.name,1)||repeat('*',greatest(char_length(l.name)-1,0)) AS 이름,
       c.closure_type, c.closure_date,
       MIN(ch.billing_month) AS 폐업후첫부과월, MAX(ch.billing_month) AS 마지막부과월,
       COUNT(*) AS 건수, SUM(ch.amount) AS 금액
FROM license_holders l
JOIN closures c ON c.id = l.closure_id AND c.deleted_at IS NULL
JOIN receivable_charges ch ON ch.member_id = l.id
WHERE c.closure_date ~ '^\d{4}-\d{2}'
  AND ch.billing_month > substring(c.closure_date,1,7) AND ch.amount > 0
GROUP BY l.id, l.name, c.closure_type, c.closure_date
ORDER BY SUM(ch.amount) DESC LIMIT 100;

-- [Q10] 폐업(status=closed)인데 폐업 이력 연결(closure_id)이 없거나, 활성(active)인데 폐업이력이 연결된 회원 수
SELECT
  SUM(CASE WHEN l.status='closed' AND l.closure_id IS NULL THEN 1 ELSE 0 END) AS 폐업인데_이력없음,
  SUM(CASE WHEN l.status='active' AND l.closure_id IS NOT NULL THEN 1 ELSE 0 END) AS 활성인데_이력연결
FROM license_holders l WHERE l.deleted_at IS NULL;

-- [Q11] 재등록 의심: 동일 이름+차량번호가 2건 이상(폐업·활성 혼재) — 건수만
SELECT COUNT(*) AS 재등록의심_그룹수 FROM (
  SELECT regexp_replace(name,'\s+','','g') n, regexp_replace(lower(vehicle_number),'[\s\-]+','','g') v
  FROM license_holders WHERE deleted_at IS NULL
  GROUP BY 1,2 HAVING COUNT(*)>1 AND COUNT(DISTINCT status)>1
) t;

-- [Q12] 비고 불일치: 미수금 비고(legacy_note)와 회원 비고(memo)의 핵심 키워드 불일치 — 유형별 건수
WITH k AS (
  SELECT l.id,
    (r.legacy_note ~ '폐업|폐지|탈퇴|양도|미발급|자격증명') AS 미수금비고_키워드,
    (l.memo        ~ '폐업|폐지|탈퇴|양도|미발급|자격증명') AS 회원비고_키워드
  FROM license_holders l JOIN receivable_profiles r ON r.member_id=l.id
  WHERE l.deleted_at IS NULL)
SELECT COUNT(*) FILTER (WHERE 미수금비고_키워드 AND NOT 회원비고_키워드) AS 미수금비고에만,
       COUNT(*) FILTER (WHERE 회원비고_키워드 AND NOT 미수금비고_키워드) AS 회원비고에만,
       COUNT(*) FILTER (WHERE 미수금비고_키워드 AND 회원비고_키워드)     AS 양쪽모두
FROM k;

-- [Q13] 동일 회원·일자·금액·방법의 유효 수납 중복 (건수만)
SELECT COUNT(*) AS 중복그룹수, COALESCE(SUM(c-1),0) AS 초과건수 FROM (
  SELECT COUNT(*) c FROM receivable_payments WHERE cancelled_at IS NULL
  GROUP BY member_id, payment_date, amount, COALESCE(method,'') HAVING COUNT(*)>1) t;
