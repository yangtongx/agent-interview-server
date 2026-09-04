from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from server.config import LLM_API_KEY, REQUIRE_RESUME_ENCRYPTION, RESUME_FILE_KEY
from server.db import get_conn
from server.observability import log_event

router = APIRouter()


@router.get("/api/health")
def api_health():
    db_ok = False
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1")
            db_ok = True
    except Exception as err:
        log_event("health_db_error", error=str(err))
    resume_ok = bool(RESUME_FILE_KEY) if REQUIRE_RESUME_ENCRYPTION else True
    payload = {
        "ok": db_ok and resume_ok,
        "db": db_ok,
        "resumeEncryption": resume_ok,
        "llmConfigured": bool(LLM_API_KEY),
    }
    return JSONResponse(payload, status_code=200 if payload["ok"] else 503)
