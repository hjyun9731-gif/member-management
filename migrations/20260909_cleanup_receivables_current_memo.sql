-- RECEIVABLES-CURRENT 내부 표식 긴급 정리용 PostgreSQL
BEGIN;

UPDATE license_holders
SET memo = BTRIM(regexp_replace(COALESCE(memo, ''), '\[RECEIVABLES-CURRENT-[^]]+\]', '', 'gi'))
WHERE memo ~* '\[RECEIVABLES-CURRENT-[^]]+\]';

UPDATE closures
SET memo = BTRIM(regexp_replace(COALESCE(memo, ''), '\[RECEIVABLES-CURRENT-[^]]+\]', '', 'gi'))
WHERE memo ~* '\[RECEIVABLES-CURRENT-[^]]+\]';

UPDATE candidates
SET memo = BTRIM(regexp_replace(COALESCE(memo, ''), '\[RECEIVABLES-CURRENT-[^]]+\]', '', 'gi'))
WHERE memo ~* '\[RECEIVABLES-CURRENT-[^]]+\]';

UPDATE transfer_ledger
SET memo = BTRIM(regexp_replace(COALESCE(memo, ''), '\[RECEIVABLES-CURRENT-[^]]+\]', '', 'gi'))
WHERE memo ~* '\[RECEIVABLES-CURRENT-[^]]+\]';

COMMIT;

SELECT 'license_holders' AS table_name, COUNT(*) AS remaining
FROM license_holders WHERE memo ~* '\[RECEIVABLES-CURRENT-[^]]+\]'
UNION ALL
SELECT 'closures', COUNT(*) FROM closures WHERE memo ~* '\[RECEIVABLES-CURRENT-[^]]+\]'
UNION ALL
SELECT 'candidates', COUNT(*) FROM candidates WHERE memo ~* '\[RECEIVABLES-CURRENT-[^]]+\]'
UNION ALL
SELECT 'transfer_ledger', COUNT(*) FROM transfer_ledger WHERE memo ~* '\[RECEIVABLES-CURRENT-[^]]+\]';
