from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from server.auth import optional_user
from server.bank import cached_etag, etag_matches, flatten_parents, load_bank
from server.deps import error
from server.observability import log_event
from server.progress_sync import load_progress, review_and_weak
from server.recommend import build_home, preferred_parent_ids

router = APIRouter()

_BANK_CACHE_CONTROL = "private, must-revalidate"


@router.get("/api/bank")
def api_bank(request: Request):
    try:
        payload = load_bank()
        etag = cached_etag()
        if etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": _BANK_CACHE_CONTROL})
        return JSONResponse(payload, headers={"ETag": etag, "Cache-Control": _BANK_CACHE_CONTROL})
    except Exception as err:
        log_event("bank_error", error=str(err))
        return error(str(err) or "题库读取失败", 500)


@router.get("/api/home")
def api_home(request: Request, audience: str = "", mastered: str = ""):
    user = optional_user(request)
    review_ids: list[str] = []
    weak_ids: list[str] = []
    mastered_ids: set[str] = set()
    resolved = audience if audience in ("unset", "transition", "experienced") else ""
    if user:
        progress = load_progress(user["id"])
        mastered_ids = set(progress["mastered"])
        review_ids, weak_ids = review_and_weak(user["id"])
        if not resolved:
            resolved = user.get("audience") or "unset"
    else:
        mastered_ids = {item for item in mastered.split(",") if item.strip()}
        if not resolved:
            resolved = "unset"
    bank = load_bank()
    parents = flatten_parents(bank["questions"])
    return build_home(
        resolved,
        bank.get("paths") or [],
        parents,
        bank["topics"],
        mastered_ids,
        preferred_parent_ids(resolved, parents),
        review_ids,
        weak_ids,
    )
