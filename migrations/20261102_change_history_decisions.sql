-- 운영 반영 전: pg_dump 백업 + 격리 DB 복구시험 + 읽기전용 ID 대조 + 영향 보고가 필요하다.
-- 원본 변경이력은 수정하지 않는다. 판정 감사기록용 테이블/인덱스만 추가한다.
CREATE TABLE IF NOT EXISTS change_history_decisions (
    id SERIAL PRIMARY KEY,
    history_id INTEGER NOT NULL REFERENCES change_history(id),
    judgement VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL,
    report_decision VARCHAR(10) NOT NULL,
    reason TEXT NOT NULL,
    evidence JSON,
    source_hash VARCHAR(64) NOT NULL,
    rule_version VARCHAR(40) NOT NULL,
    duplicate_of_id INTEGER REFERENCES change_history(id),
    created_by VARCHAR(100) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_change_history_decisions_history_id
    ON change_history_decisions(history_id);
