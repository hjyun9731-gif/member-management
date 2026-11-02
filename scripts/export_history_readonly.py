"""Railway DB 읽기전용 내보내기. 앱·마이그레이션을 import하지 않는다.

실행: py export_history_readonly.py
Railway PostgreSQL의 DATABASE_PUBLIC_URL을 화면에 숨겨진 입력란에 붙여넣는다.
출력: railway_readonly_snapshot.json. URL/비밀번호는 출력하거나 파일에 저장하지 않는다.
"""
import argparse
import datetime as dt
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlparse

FORMAT_VERSION = 2
TABLE_FIELDS = {
    "change_history": ["id", "change_type", "region", "vehicle_number", "name", "before_value", "after_value",
        "change_date", "receipt_date", "memo", "judgement", "judge_reason", "member_id", "upload_id", "raw_data",
        "created_at", "updated_at", "deleted_at"],
    "change_history_decisions": ["id", "history_id", "judgement", "status", "report_decision", "reason",
        "source_hash", "rule_version", "duplicate_of_id", "created_at"],
    "license_holders": ["id", "name", "region", "vehicle_number", "vehicle_type", "address", "official_address",
        "company_name", "affiliated_company", "status", "category", "closure_id", "deleted_at"],
    "closures": ["id", "member_id", "name", "region", "vehicle_number", "closure_type", "closure_date",
        "receipt_date", "reason", "management_number", "deleted_at"],
    "member_edit_logs": ["id", "member_id", "name", "vehicle_number", "field_name", "old_value", "new_value",
        "change_type", "record_to_change_history", "created_at"],
    "monthly_report_entries": ["id", "year", "month", "custom_data", "updated_at"],
    "report_field_defs": ["id", "key", "label", "field_type", "auto_path", "is_active"],
}
REQUIRED_HISTORY_FIELDS = {"id", "change_type", "region", "vehicle_number", "name", "before_value",
                           "after_value", "change_date", "receipt_date", "memo", "deleted_at"}
RAW_KEYS = re.compile(r"주소|상세|변경전|변경후|변경내용|변경사항|변경사유|처리사유|비고|메모|이관|전출|차종|구조변경|^source$|^change_reason[s]?$|^auto_change_type$|^(?:before_?value|after_?value|old_address|new_address)$", re.I)


def sanitize_raw(raw):
    if not isinstance(raw, dict):
        return {}
    output = {}
    for k, v in raw.items():
        if not RAW_KEYS.search(re.sub(r"\s", "", str(k))):
            continue
        if re.search(r"계좌|주민|비밀번호|토큰|password|secret|token|database_url", str(k), re.I):
            continue
        if str(k) == "change_reasons":
            if isinstance(v, dict):
                output[str(k)] = {f: choice for f, choice in v.items()
                    if f in ("address", "official_address", "company_name", "affiliated_company", "vehicle_number", "vehicle_type")
                    and isinstance(choice, str) and choice in ("actual", "correction", "supplement")}
        elif isinstance(v, (str, int, float, bool)) or v is None:
            if isinstance(v, str) and re.search(r"postgres(?:ql)?(?:\+\w+)?://|DATABASE_URL|password=", v, re.I):
                continue
            output[str(k)] = v
    return output


def normalize_url(value):
    value = str(value).strip()
    value = value.replace("postgresql+psycopg2://", "postgresql://", 1).replace("postgresql+psycopg://", "postgresql://", 1)
    parsed = urlparse(value)
    if parsed.scheme not in ("postgres", "postgresql") or not parsed.hostname:
        raise ValueError("PostgreSQL URL 형식이 아닙니다.")
    if parsed.hostname.endswith(".railway.internal") and not os.environ.get("RAILWAY_ENVIRONMENT"):
        raise ValueError("PRIVATE_URL_FROM_LOCAL")
    return value


def capture(connection):
    """이미 연결된 psycopg2 connection을 읽기전용 스냅샷으로 고정한다."""
    connection.set_session(readonly=True, isolation_level="REPEATABLE READ", autocommit=False)
    tables, schema, warnings = {}, {}, []
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '60s'")
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SELECT current_setting('transaction_read_only'), current_setting('transaction_isolation')")
            mode, isolation = cursor.fetchone()
            if mode != "on" or isolation != "repeatable read":
                raise RuntimeError("읽기전용 트랜잭션을 확인할 수 없습니다.")
            cursor.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = current_schema()")
            for table, column in cursor.fetchall():
                schema.setdefault(table, []).append(column)
            if not REQUIRED_HISTORY_FIELDS <= set(schema.get("change_history", [])):
                raise RuntimeError("변경이력 필수 스키마가 일치하지 않습니다.")
            for table, wanted in TABLE_FIELDS.items():
                available = set(schema.get(table, []))
                if not available:
                    warnings.append(f"{table}: 테이블 없음")
                    tables[table] = []
                    continue
                fields = [name for name in wanted if name in available]
                # 테이블/컬럼명은 코드의 고정 허용목록만 사용한다.
                sql = 'SELECT ' + ', '.join('"' + n + '"' for n in fields) + ' FROM "' + table + '" ORDER BY "id"'
                if table == "member_edit_logs":
                    if "field_name" not in fields:
                        tables[table] = []; warnings.append("member_edit_logs: 필드명 컬럼 없음"); continue
                    sql = sql.replace(' ORDER BY "id"', ' WHERE "field_name" = ANY(%s) ORDER BY "id"')
                    cursor.execute(sql, (["address", "official_address", "company_name", "affiliated_company",
                                          "vehicle_number", "vehicle_type", "structure_change", "fuel_type", "name", "region"],))
                else:
                    cursor.execute(sql)
                rows = [dict(zip(fields, values)) for values in cursor.fetchall()]
                if table == "change_history":
                    for row in rows:
                        row["raw_data"] = sanitize_raw(row.get("raw_data"))
                tables[table] = rows
            protected = {r["key"] for r in tables.get("report_field_defs", []) if str(r.get("auto_path", "")).startswith("admin_work.")}
            for row in tables.get("monthly_report_entries", []):
                row["custom_data"] = {k: v for k, v in (row.get("custom_data") or {}).items() if k in protected}
            cursor.execute("SHOW transaction_read_only")
            if cursor.fetchone()[0] != "on":
                raise RuntimeError("트랜잭션이 읽기전용이 아닙니다.")
        return {"format_version": FORMAT_VERSION, "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "read_only_verified": True, "isolation": isolation, "db_writes": 0,
                "schema": {k: schema.get(k, []) for k in TABLE_FIELDS},
                "tables": tables, "warnings": warnings}
    finally:
        connection.rollback()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default="railway_readonly_snapshot.json")
    a = p.parse_args()
    try:
        import psycopg2
    except ImportError:
        print("필수 패키지 설치 후 다시 실행: py -m pip install psycopg2-binary")
        return 2
    value = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not value:
        print("Railway PostgreSQL의 DATABASE_PUBLIC_URL을 복사하세요. 입력은 표시·저장되지 않습니다.")
        value = getpass.getpass("DB 연결 URL: ")
    try:
        url = normalize_url(value)
        conn = psycopg2.connect(url, connect_timeout=15,
            options="-c default_transaction_read_only=on -c statement_timeout=60000", application_name="history_readonly_export")
        try:
            result = capture(conn)
        finally:
            conn.close()
    except ValueError as exc:
        if str(exc) == "PRIVATE_URL_FROM_LOCAL":
            print("내부 주소는 PC에서 연결할 수 없습니다. Railway의 DATABASE_PUBLIC_URL을 사용하세요.")
        else:
            print("연결 URL 형식을 확인하세요. URL/비밀번호는 채팅에 보내지 마세요.")
        return 1
    except Exception as exc:
        print(f"내보내기 중단: {type(exc).__name__}. DB 쓰기 없음. 접속 문자열은 출력하지 않았습니다.")
        return 1
    path = Path(a.out).resolve()
    data = json.dumps(result, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    path.write_bytes(data)
    print(f"완료: {path.name}, 변경이력 {len(result['tables']['change_history'])}건, 읽기전용 확인, DB 쓰기 0건")
    print("이 JSON 파일만 전달하세요. 전체 DB 백업 파일이나 연결 URL은 필요하지 않습니다.")
    print("파일 SHA256:", hashlib.sha256(data).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main())
