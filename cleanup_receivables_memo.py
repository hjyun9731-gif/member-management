import os
import psycopg2

url = os.environ.get("DATABASE_PUBLIC_URL")
if not url:
    raise SystemExit("DATABASE_PUBLIC_URL 없음")

pattern = r'\[RECEIVABLES-CURRENT-[^]]+\]'

conn = psycopg2.connect(url)

try:
    with conn:
        with conn.cursor() as cur:
            for table in ("closures", "license_holders"):

                cur.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE memo ~* %s",
                    (pattern,)
                )
                before = cur.fetchone()[0]

                cur.execute(
                    f"""
                    UPDATE {table}
                    SET memo = NULLIF(
                        BTRIM(
                            regexp_replace(
                                COALESCE(memo, ''),
                                %s,
                                '',
                                'gi'
                            )
                        ),
                        ''
                    )
                    WHERE memo ~* %s
                    """,
                    (pattern, pattern)
                )

                changed = cur.rowcount

                cur.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE memo ~* %s",
                    (pattern,)
                )
                after = cur.fetchone()[0]

                print(
                    f"{table}: 정리 전 {before}건 / "
                    f"수정 {changed}건 / 남은 것 {after}건"
                )

    print("완료")
finally:
    conn.close()
