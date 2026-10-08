"""Railway-safe ASGI entrypoint.

Purpose:
- Bind the Railway port and answer /health immediately.
- Load the existing FastAPI app (app.main:app) in the background.
- Delegate every normal request to the existing app once it is ready.
- If the real app fails to import/start, terminate the process so Railway does
  not promote a broken deployment.

This file does not replace or duplicate any member/receivables business logic.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import threading
import time
import traceback
from typing import Any, Optional

_real_app: Optional[Any] = None
_patch_app: Optional[Any] = None
PATCH_PREFIX = "/api/receivables/reconcile-20261008"
_ready = threading.Event()
_failed = threading.Event()
_error_summary = ""
_loader_started = False
_loader_lock = threading.Lock()


def _log(message: str) -> None:
    print(f"[railway-entry] {message}", file=sys.stderr, flush=True)


def _load_real_app() -> None:
    """Import the existing application and run its startup hooks once."""
    global _real_app, _patch_app, _error_summary
    try:
        started = time.monotonic()
        _log("loading app.main:app ...")
        module = importlib.import_module("app.main")
        candidate = getattr(module, "app")

        # 2026-10-07 수납·미수금 보정 라우터는 기존 app.main을 덮어쓰지 않고
        # Railway 진입점에서 추가한다. 같은 경로가 이미 있으면 중복 등록하지 않는다.
        receivables_reconcile = importlib.import_module("app.routers.receivables_reconcile_20261008")

        # app.main already has a final catch-all route (/{p:path}).  FastAPI/Starlette
        # evaluates routes in registration order, so adding this router to candidate
        # here would place it *after* the catch-all and /api/... would be swallowed as
        # a 404.  Keep a tiny dedicated FastAPI app for the reconcile endpoints and
        # dispatch those paths from this ASGI entrypoint before delegating to app.main.
        from fastapi import FastAPI
        patch_app = FastAPI()
        patch_app.include_router(receivables_reconcile.router)
        _patch_app = patch_app

        # Because uvicorn owns this bootstrap ASGI app, the nested FastAPI app's
        # lifespan is not invoked automatically. Run the existing startup hooks
        # explicitly once. Current app.main startup is intentionally non-blocking
        # and launches DB maintenance in daemon threads.
        router = getattr(candidate, "router", None)
        startup = getattr(router, "startup", None)
        if startup is not None:
            asyncio.run(startup())

        _real_app = candidate
        _ready.set()
        _log(f"real app ready in {time.monotonic() - started:.2f}s")

        # DB 보정은 웹 서버 준비를 막지 않도록 별도 스레드에서 실행한다.
        # 실패 시 운영 앱은 계속 살아 있고 /api/receivables/patch-20261007/apply 에서 재실행 가능하다.
        def _apply_receivables_reconcile():
            for attempt in range(1, 7):
                try:
                    result = receivables_reconcile.apply_once(force=False)
                    _log(
                        "receivables 20261008 MONTHLY V2 reconcile: "
                        f"status={result.get('status')} "
                        f"active={result.get('result', {}).get('active', {}).get('matched', 0)} "
                        f"closures={result.get('result', {}).get('closures', {}).get('matched', 0)}"
                    )
                    return
                except Exception as exc:
                    _log(f"receivables 20261008 FINAL reconcile attempt {attempt}/6 failed: {type(exc).__name__}: {exc}")
                    if attempt < 6:
                        time.sleep(5)

        threading.Thread(
            target=_apply_receivables_reconcile,
            name="receivables-reconcile-20261008-monthly-v2",
            daemon=True,
        ).start()
    except BaseException as exc:  # startup failure must fail the deployment
        _error_summary = f"{type(exc).__name__}: {exc}"
        _failed.set()
        _log("REAL APP STARTUP FAILED")
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        # Let Railway capture the traceback, then terminate. This prevents a
        # health-only shell from replacing the last known-good deployment.
        time.sleep(2)
        os._exit(1)


def _ensure_loader_started(delay: float = 0.0) -> None:
    """Start the real app loader once, optionally after a short delay.

    Railway must be able to bind the port and answer /health before any heavy
    application import or database work can interfere with process startup.
    """
    global _loader_started
    if _loader_started:
        return
    with _loader_lock:
        if _loader_started:
            return
        _loader_started = True

        if delay > 0:
            timer = threading.Timer(delay, _load_real_app)
            timer.name = "real-app-loader-delay"
            timer.daemon = True
            timer.start()
            return

        threading.Thread(
            target=_load_real_app,
            name="real-app-loader",
            daemon=True,
        ).start()


def _response(status: int, payload: dict[str, Any]):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return (
        {"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"cache-control", b"no-store"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]},
        {"type": "http.response.body", "body": body},
    )


class RailwayEntryApp:
    async def __call__(self, scope, receive, send):
        scope_type = scope.get("type")

        if scope_type == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    # Tell Uvicorn startup is complete first, then load the heavy
                    # FastAPI app in the background. The short delay gives Railway
                    # time to bind the socket and reach /health reliably.
                    _ensure_loader_started(delay=0.5)
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    # Run existing app shutdown hooks when available.
                    if _real_app is not None:
                        try:
                            router = getattr(_real_app, "router", None)
                            shutdown = getattr(router, "shutdown", None)
                            if shutdown is not None:
                                await shutdown()
                        except Exception:
                            traceback.print_exc(file=sys.stderr)
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            
        if scope_type != "http":
            if _real_app is not None and _ready.is_set():
                await _real_app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Railway liveness endpoint must stay completely independent from the
        # real app loader and database. Do not start heavy imports from /health.
        # Railway liveness endpoint: port becomes reachable immediately. If the
        # actual app has a fatal import/startup error the process exits, so a
        # broken deployment cannot remain promoted just because this is 200.
        if path == "/health":
            state = "ready" if _ready.is_set() else "starting"
            start_msg, body_msg = _response(200, {
                "status": "ok",
                "app": state,
                "failed": _failed.is_set(),
            })
            await send(start_msg)
            await send(body_msg)
            return

        # Any non-health request may ensure the real app loader has started.
        _ensure_loader_started()

        # Route reconciliation API before app.main.  app.main contains a catch-all
        # route registered during import; dispatching here prevents that catch-all
        # from masking the dynamically-added reconcile endpoints as 404.
        if path.startswith(PATCH_PREFIX):
            if _ready.is_set() and _patch_app is not None:
                await _patch_app(scope, receive, send)
                return

        if _ready.is_set() and _real_app is not None:
            await _real_app(scope, receive, send)
            return

        # Normal traffic is not silently served by an incomplete app.
        start_msg, body_msg = _response(503, {
            "status": "starting",
            "message": "서버 초기화 중입니다. 잠시 후 다시 시도해주세요.",
            "error": _error_summary if _failed.is_set() else "",
        })
        await send(start_msg)
        await send(body_msg)


app = RailwayEntryApp()

# IMPORTANT: do not start app.main during module import. Uvicorn/Railway must
# first finish process startup and bind the service port. The real app loader
# is started from ASGI lifespan (with a short delay) or the first non-health
# request.
