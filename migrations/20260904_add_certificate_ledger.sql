-- 자격증명 발급대장 전용 신규 테이블만 생성한다.
-- 기존 테이블 ALTER / UPDATE / DELETE / DROP 없음.

CREATE TABLE IF NOT EXISTS certificate_issuance_ledger (
    id SERIAL PRIMARY KEY,
    candidate_id INTEGER UNIQUE,
    member_id INTEGER,
    region VARCHAR(50),
    vehicle_number VARCHAR(50),
    name VARCHAR(100) NOT NULL,
    qualification_number VARCHAR(100),
    document_number VARCHAR(100) UNIQUE,
    approval_date VARCHAR(50),
    certificate_issue_date VARCHAR(50),
    status VARCHAR(20) NOT NULL DEFAULT '인가대기'
        CHECK (status IN ('인가대기', '인가완료', '발급완료')),
    latest_operator VARCHAR(100),
    created_by VARCHAR(100),
    approved_at TIMESTAMPTZ,
    issued_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_certificate_issuance_candidate ON certificate_issuance_ledger(candidate_id);
CREATE INDEX IF NOT EXISTS ix_certificate_issuance_member ON certificate_issuance_ledger(member_id);
CREATE INDEX IF NOT EXISTS ix_certificate_issuance_status ON certificate_issuance_ledger(status);
CREATE INDEX IF NOT EXISTS ix_certificate_issuance_document ON certificate_issuance_ledger(document_number);

CREATE TABLE IF NOT EXISTS certificate_issuance_history (
    id SERIAL PRIMARY KEY,
    ledger_id INTEGER NOT NULL REFERENCES certificate_issuance_ledger(id) ON DELETE CASCADE,
    event_type VARCHAR(30) NOT NULL,
    from_status VARCHAR(20),
    to_status VARCHAR(20) NOT NULL,
    operator VARCHAR(100),
    memo TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_certificate_issuance_history_ledger ON certificate_issuance_history(ledger_id);

-- 예정자 저장 직후에는 아직 자격증명발급번호가 없을 수 있으므로 NULL 허용.
ALTER TABLE certificate_issuance_ledger ALTER COLUMN document_number DROP NOT NULL;
