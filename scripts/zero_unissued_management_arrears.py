"""자격증명 미발급 회원의 관리비 미수금 0원 정정 — 서비스(app/receivable_adjustments.py)를 쓰는 명령줄 도구.

  · 기본(미리보기): DB를 바꾸지 않고 대상/금액/plan_digest 를 출력하고 CSV 를 저장한다.
        python -m scripts.zero_unissued_management_arrears [--csv 파일.csv] [--exclude 12,34]
  · 적용(1회): 백업 확보 후, 미리보기에서 나온 plan_digest 와 함께
        python -m scripts.zero_unissued_management_arrears --apply --plan-digest <digest> \
            --confirm ZERO_UNISSUED_MANAGEMENT --backup-confirmed
  · 정정은 실제 입금이 아니므로 receivable_payments 를 만들지 않고 receivable_adjustments 에만 기록한다.
"""
import argparse
import csv
import json

from app.database import SessionLocal
from app import receivable_adjustments as svc


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--plan-digest", default="")
    p.add_argument("--confirm", default="")
    p.add_argument("--backup-confirmed", action="store_true")
    p.add_argument("--exclude", default="", help="제외할 회원 ID(쉼표)")
    p.add_argument("--csv", default="", help="미리보기 보고서 CSV 저장 경로")
    p.add_argument("--actor", default="cli")
    a = p.parse_args()
    exclude = [int(x) for x in a.exclude.split(",") if x.strip()]
    db = SessionLocal()
    try:
        if a.apply:
            res = svc.apply_plan(db, plan_digest=a.plan_digest, confirm=a.confirm, backup_confirmed=a.backup_confirmed,
                                 actor=a.actor, exclude_member_ids=exclude)
            db.commit()
            print(json.dumps({"mode": "APPLIED", **res}, ensure_ascii=False, indent=2))
        else:
            plan = svc.build_plan(db, exclude)
            db.rollback()
            if a.csv:
                with open(a.csv, "w", newline="", encoding="utf-8-sig") as f:
                    w = csv.writer(f)
                    cols = ["member_id", "name", "vehicle_number", "category", "member_status", "certificate_issue_date", "certificate_number",
                            "account_type", "baseline", "charges", "payments", "adjustments", "balance_before", "planned_adjustment",
                            "balance_after", "decision", "reason"]
                    w.writerow(cols)
                    for r in plan["rows"]:
                        w.writerow([r[c] for c in cols])
            print(json.dumps({"mode": "PREVIEW(DB 변경 없음)", "plan_digest": plan["plan_digest"], "summary": plan["summary"]},
                             ensure_ascii=False, indent=2))
    except svc.AdjustmentError as e:
        db.rollback()
        raise SystemExit(f"중단(아무것도 바뀌지 않음): {e}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
