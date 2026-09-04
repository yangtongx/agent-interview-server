from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from server.admin import (
    admin_stats,
    get_question,
    list_questions,
    list_releases,
    list_topics,
    patch_question,
    publish_release,
    require_admin,
)
from server.deps import read_object

router = APIRouter()
ADMIN_HTML = Path(__file__).resolve().parent.parent / "admin.html"


@router.get("/api/admin/stats")
def api_admin_stats(request: Request):
    require_admin(request)
    return admin_stats()


@router.get("/api/admin/topics")
def api_admin_topics(request: Request):
    require_admin(request)
    return {"items": list_topics()}


@router.get("/api/admin/questions")
def api_admin_questions(
    request: Request,
    q: str = "",
    status: str = "",
    topicId: str = "",
    page: int = 1,
    size: int = 20,
):
    require_admin(request)
    return list_questions(q, status, topicId, page, size)


@router.get("/api/admin/questions/{qid}")
def api_admin_question(request: Request, qid: str):
    require_admin(request)
    return get_question(qid)


@router.patch("/api/admin/questions/{qid}")
async def api_admin_patch_question(request: Request, qid: str):
    require_admin(request)
    body = await read_object(request)
    return patch_question(qid, body)


@router.get("/api/admin/releases")
def api_admin_releases(request: Request):
    require_admin(request)
    return {"items": list_releases()}


@router.post("/api/admin/releases")
async def api_admin_publish(request: Request):
    require_admin(request)
    body = await read_object(request)
    version = body.get("version") if isinstance(body.get("version"), str) else ""
    summary = body.get("summary") if isinstance(body.get("summary"), str) else ""
    return publish_release(version, summary)


@router.get("/admin")
def admin_page():
    if not ADMIN_HTML.is_file():
        raise HTTPException(status_code=404, detail="中台页面不存在。")
    return HTMLResponse(ADMIN_HTML.read_text(encoding="utf-8"))
