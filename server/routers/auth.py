from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from server.auth import (
    bearer_token,
    login_account,
    login_device,
    load_me,
    register_account,
    require_user,
    revoke_session,
    update_me,
)
from server.deps import audience, auth_limited, client_ip, device_id, error, platform, read_object
from server.progress_sync import add_practice, load_progress, merge_progress

router = APIRouter()


@router.post("/api/auth/device")
async def api_auth_device(request: Request):
    if auth_limited.too_many(client_ip(request)):
        return error("登录太频繁，稍后再试。", 429)
    body = await read_object(request)
    did = device_id(body)
    if not did:
        raise HTTPException(status_code=400, detail="需要 deviceId。")
    name = body.get("deviceName") if isinstance(body.get("deviceName"), str) else ""
    return login_device(did, platform(body), name, audience(body))


@router.post("/api/auth/register")
async def api_auth_register(request: Request):
    if auth_limited.too_many(client_ip(request)):
        return error("注册太频繁，稍后再试。", 429)
    body = await read_object(request)
    provider = body.get("provider") if isinstance(body.get("provider"), str) else ""
    uid = body.get("uid") if isinstance(body.get("uid"), str) else ""
    password = body.get("password") if isinstance(body.get("password"), str) else ""
    display = body.get("displayName") if isinstance(body.get("displayName"), str) else ""
    return register_account(
        provider,
        uid,
        password,
        display,
        platform(body),
        device_id(body),
        audience(body),
    )


@router.post("/api/auth/login")
async def api_auth_login(request: Request):
    if auth_limited.too_many(client_ip(request)):
        return error("登录太频繁，稍后再试。", 429)
    body = await read_object(request)
    provider = body.get("provider") if isinstance(body.get("provider"), str) else ""
    uid = body.get("uid") if isinstance(body.get("uid"), str) else ""
    password = body.get("password") if isinstance(body.get("password"), str) else ""
    return login_account(provider, uid, password, platform(body), device_id(body))


@router.post("/api/auth/logout")
def api_auth_logout(request: Request):
    revoke_session(bearer_token(request))
    return {"ok": True}


@router.get("/api/me")
def api_me(request: Request):
    user = require_user(request)
    return load_me(user["id"])


@router.patch("/api/me")
@router.put("/api/me")
async def api_me_update(request: Request):
    user = require_user(request)
    body = await read_object(request)
    return update_me(user["id"], body)


@router.get("/api/progress")
def api_progress(request: Request):
    user = require_user(request)
    return load_progress(user["id"])


@router.put("/api/progress")
async def api_progress_put(request: Request):
    user = require_user(request)
    body = await read_object(request)
    return merge_progress(user["id"], body)


@router.post("/api/practice")
async def api_practice(request: Request):
    user = require_user(request)
    body = await read_object(request)
    question_id = body.get("questionId") if isinstance(body.get("questionId"), str) else ""
    answer = body.get("answerText") if isinstance(body.get("answerText"), str) else ""
    mode = body.get("inputMode") if isinstance(body.get("inputMode"), str) else "text"
    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    if not question_id:
        raise HTTPException(status_code=400, detail="需要 questionId。")
    add_practice(user["id"], user.get("deviceId") or None, question_id, answer, mode, result)
    return {"ok": True}
