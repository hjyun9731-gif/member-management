"""RECEIVABLES-CURRENT 내부 표식이 회원/폐업 비고에 저장되는 문제 방지 및 기존 데이터 정리."""
import logging
import re
import threading
import time

from sqlalchemy import event
from app import models
from app.database import SessionLocal

logger = logging.getLogger(__name__)
_MARKER_RE = re.compile(r"\[RECEIVABLES-CURRENT-[^\]]+\]", re.IGNORECASE)


def sanitize_receivables_memo(value):
    if value is None:
        return value
    text = str(value)
    cleaned = _MARKER_RE.sub("", text)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"^[\s,;|·\-]+", "", cleaned)
    cleaned = re.sub(r"[\s,;|·\-]+$", "", cleaned)
    return cleaned.strip()


def _sanitize_target_memo(mapper, connection, target):
    if not hasattr(target, "memo"):
        return
    old = getattr(target, "memo", None)
    new = sanitize_receivables_memo(old)
    if new != old:
        setattr(target, "memo", new)


for _model in (
    getattr(models, "LicenseHolder", None),
    getattr(models, "Closure", None),
    getattr(models, "Candidate", None),
    getattr(models, "TransferLedger", None),
):
    if _model is not None and hasattr(_model, "memo"):
        event.listen(_model, "before_insert", _sanitize_target_memo, propagate=True)
        event.listen(_model, "before_update", _sanitize_target_memo, propagate=True)


def cleanup_existing_receivables_memos():
    db = SessionLocal()
    stats = {}
    try:
        targets = (
            ("license_holders", getattr(models, "LicenseHolder", None)),
            ("closures", getattr(models, "Closure", None)),
            ("candidates", getattr(models, "Candidate", None)),
            ("transfer_ledger", getattr(models, "TransferLedger", None)),
        )
        total_updated = 0
        for label, model in targets:
            if model is None or not hasattr(model, "memo"):
                continue
            rows = db.query(model).filter(
                model.memo.isnot(None),
                model.memo.ilike("%RECEIVABLES-CURRENT-%"),
            ).all()
            updated = 0
            for row in rows:
                old = row.memo
                new = sanitize_receivables_memo(old)
                if new != old:
                    row.memo = new
                    updated += 1
            stats[label] = updated
            total_updated += updated

        if total_updated:
            db.commit()
        else:
            db.rollback()

        stats["total_updated"] = total_updated
        logger.warning("RECEIVABLES-CURRENT 비고 오염 정리 완료: %s", stats)
        return stats
    except Exception:
        db.rollback()
        logger.exception("RECEIVABLES-CURRENT 비고 오염 정리 실패")
        raise
    finally:
        db.close()


def _background_cleanup():
    time.sleep(2)
    try:
        cleanup_existing_receivables_memos()
    except Exception:
        pass


def start_background_cleanup_once():
    threading.Thread(
        target=_background_cleanup,
        name="receivables-memo-cleanup",
        daemon=True,
    ).start()


start_background_cleanup_once()
