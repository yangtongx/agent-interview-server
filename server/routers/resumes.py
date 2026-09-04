from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from starlette.datastructures import UploadFile

from server.auth import require_user
from server.db import get_conn
from server.deps import client_ip, error, read_object, resume_turn_limited, resume_upload_limited
from server.observability import log_event
from server.resume_files import ResumeStorageError, reject_reason, resume_dir, write_stored
from server.resume_mock import create_session, finish_session, take_turn
from server.resumes import (
    NOT_FOUND,
    confirm_resume,
    create_resume_row,
    get_resume_for_user,
    insert_blank_profile,
    list_resumes,
    parse_resume_id,
    public_resume,
    replace_and_load,
    run_parse_job,
)

router = APIRouter()


def _resume_stored_name(filename: str) -> str:
    suffix = Path(Path(filename or "").name).suffix.lower()
    if suffix in {".pdf", ".docx", ".jpg", ".jpeg", ".png", ".webp"}:
        return f"upload{suffix}"
    return "upload.bin"


@router.post("/api/resumes")
async def api_resumes_upload(request: Request, background_tasks: BackgroundTasks):
    user = require_user(request)
    if resume_upload_limited.too_many(client_ip(request)):
        return error("上传太频繁，稍等一分钟再试。", 429)
    try:
        form = await request.form()
    except Exception:
        return error("请用表单上传简历。", 400)
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            return error("没有收到文件。", 400)
        filename = upload.filename.strip() if upload.filename and upload.filename.strip() else "upload.bin"
        mime = (upload.content_type or "").strip()
        data = await upload.read()
    finally:
        await form.close()
    reason = reject_reason(filename, mime, len(data))
    if reason:
        return error(reason, 400)
    with get_conn() as conn:
        row = create_resume_row(conn, user["id"], filename, mime or None, len(data), None)
        resume_id = row["id"]
        dest = resume_dir(str(user["id"]), str(resume_id)) / _resume_stored_name(filename)
        try:
            write_stored(dest, data)
        except ResumeStorageError as err:
            raise HTTPException(status_code=503, detail="简历存储未加密，已拒绝上传。") from err
        conn.execute(
            "UPDATE resumes SET storage_key = %s, updated_at = now() WHERE id = %s",
            (str(dest), resume_id),
        )
        insert_blank_profile(conn, resume_id)
    background_tasks.add_task(run_parse_job, str(resume_id))
    log_event("resume_upload", resumeId=str(resume_id))
    return {"id": str(resume_id), "parseStatus": "uploaded"}


@router.get("/api/resumes")
def api_resumes_list(request: Request):
    user = require_user(request)
    with get_conn() as conn:
        return list_resumes(conn, user["id"])


@router.get("/api/resumes/{resume_id}")
def api_resume_get(request: Request, resume_id: str):
    user = require_user(request)
    if parse_resume_id(resume_id) is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    with get_conn() as conn:
        loaded = get_resume_for_user(conn, user["id"], resume_id)
        if not loaded:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        row, profile, jobs, projects = loaded
        return public_resume(row, profile, jobs, projects)


@router.put("/api/resumes/{resume_id}")
async def api_resume_put(request: Request, resume_id: str):
    user = require_user(request)
    body = await read_object(request)
    with get_conn() as conn:
        return replace_and_load(conn, user["id"], resume_id, body)


@router.post("/api/resumes/{resume_id}/confirm")
def api_resume_confirm(request: Request, resume_id: str):
    user = require_user(request)
    with get_conn() as conn:
        return confirm_resume(conn, user["id"], resume_id)


@router.post("/api/mock/sessions")
async def api_mock_sessions_create(request: Request):
    user = require_user(request)
    body = await read_object(request)
    return create_session(user, body)


@router.post("/api/mock/sessions/{session_id}/turn")
async def api_mock_sessions_turn(request: Request, session_id: str):
    user = require_user(request)
    if resume_turn_limited.too_many(client_ip(request)):
        return error("出题太频繁，稍等一分钟再试。", 429)
    body = await read_object(request)
    return await take_turn(user, session_id, body)


@router.post("/api/mock/sessions/{session_id}/finish")
def api_mock_sessions_finish(request: Request, session_id: str):
    user = require_user(request)
    return finish_session(user, session_id)
