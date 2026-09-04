from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import UploadFile

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
from server.auth import (
    login_account,
    login_device,
    load_me,
    optional_user,
    require_user,
    revoke_session,
    bearer_token,
    register_account,
    update_me,
)
from server.bank import flatten_parents, load_bank
from server.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, MAX_AUDIO_BYTES, MAX_TTS_CHARS, REQUIRE_RESUME_ENCRYPTION, RESUME_FILE_KEY, WEB_ROOT
from server.db import apply_migrations, close_pool, get_conn
from server.egress import UnsafeUrl, validate_provider_url
from server.http_client import async_http, close_async_http, close_http
from server.observability import RequestContextMiddleware, log_event, setup_logging
from server.judge import (
    build_judge_messages,
    chat_model_for,
    completions_url,
    extract_json_object,
    looks_like_html,
    mock_judge,
    normalize_judge_result,
    parse_json_text,
)
from server.progress_sync import add_practice, load_progress, merge_progress, review_and_weak
from server.rate_limit import RateLimiter, client_ip
from server.recommend import build_home, preferred_parent_ids
from server.resume_files import ResumeStorageError, assert_resume_storage_ready, reject_reason, resume_dir, write_stored
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
from server.seed import bootstrap_if_empty
from server.speech import mime_to_audio_meta
from server.transcribe import transcribe_audio
from server.tts import synthesize_speech

judge_limited = RateLimiter(20)
tts_limited = RateLimiter(120)
asr_limited = RateLimiter(10)
judge_global = RateLimiter(300)
tts_global = RateLimiter(600)
asr_global = RateLimiter(120)
auth_limited = RateLimiter(40)
resume_upload_limited = RateLimiter(10)
resume_turn_limited = RateLimiter(20)

ADMIN_HTML = Path(__file__).with_name("admin.html")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    assert_resume_storage_ready()
    apply_migrations()
    bootstrap_if_empty()
    yield
    close_http()
    await close_async_http()
    close_pool()


app = FastAPI(title="阿里 AI 面试官", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _server_llm() -> dict[str, str]:
    return {"apiKey": LLM_API_KEY, "baseUrl": LLM_BASE_URL, "model": LLM_MODEL}


def _ai_limited(user_limiter: RateLimiter, global_limiter: RateLimiter, user: dict[str, Any]) -> bool:
    return user_limiter.too_many(f"user:{user['id']}") or global_limiter.too_many("all")


def _provider_llm(api_key_in: str, base_url_in: str, model_in: str) -> tuple[str, str, str]:
    server = _server_llm()
    body_key = api_key_in.strip()
    api_key = body_key or server["apiKey"]
    raw_base = base_url_in.strip() if body_key and base_url_in.strip() else server["baseUrl"]
    try:
        base_url = validate_provider_url(raw_base)
    except UnsafeUrl as err:
        raise HTTPException(status_code=400, detail="接口地址不被允许。") from err
    requested_model = model_in.strip() if body_key and model_in.strip() else ""
    return api_key, base_url, requested_model or server["model"]


def _error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException):
    detail = exc.detail
    message = detail if isinstance(detail, str) else "请求无效。"
    return JSONResponse({"error": message}, status_code=exc.status_code)


async def _read_object(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是 JSON。")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体不是 JSON。")
    return body


def _platform(body: dict[str, Any]) -> str:
    value = body.get("platform")
    return value if value in ("harmony", "web", "android", "ios") else "web"


def _device_id(body: dict[str, Any]) -> str | None:
    value = body.get("deviceId")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _audience(body: dict[str, Any]) -> str:
    value = body.get("audience")
    return value if value in ("unset", "transition", "experienced") else "unset"


def _record_practice(request: Request, body: dict[str, Any], user_answer: str, result: dict[str, Any]) -> None:
    user = optional_user(request)
    if not user:
        return
    question_id = body.get("questionId")
    if not isinstance(question_id, str) or not question_id.strip():
        return
    mode = body.get("inputMode") if isinstance(body.get("inputMode"), str) else "text"
    try:
        add_practice(
            user["id"],
            user.get("deviceId") or None,
            question_id.strip(),
            user_answer,
            mode,
            result,
        )
    except HTTPException:
        return
    except Exception as err:
        log_event("practice_error", error=str(err))


@app.get("/api/health")
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


@app.get("/api/bank")
def api_bank():
    try:
        payload = load_bank()
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})
    except Exception as err:
        log_event("bank_error", error=str(err))
        return _error(str(err) or "题库读取失败", 500)


@app.get("/api/home")
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


@app.post("/api/auth/device")
async def api_auth_device(request: Request):
    if auth_limited.too_many(client_ip(request)):
        return _error("登录太频繁，稍后再试。", 429)
    body = await _read_object(request)
    device_id = _device_id(body)
    if not device_id:
        raise HTTPException(status_code=400, detail="需要 deviceId。")
    name = body.get("deviceName") if isinstance(body.get("deviceName"), str) else ""
    return login_device(device_id, _platform(body), name, _audience(body))


@app.post("/api/auth/register")
async def api_auth_register(request: Request):
    if auth_limited.too_many(client_ip(request)):
        return _error("注册太频繁，稍后再试。", 429)
    body = await _read_object(request)
    provider = body.get("provider") if isinstance(body.get("provider"), str) else ""
    uid = body.get("uid") if isinstance(body.get("uid"), str) else ""
    password = body.get("password") if isinstance(body.get("password"), str) else ""
    display = body.get("displayName") if isinstance(body.get("displayName"), str) else ""
    return register_account(
        provider,
        uid,
        password,
        display,
        _platform(body),
        _device_id(body),
        _audience(body),
    )


@app.post("/api/auth/login")
async def api_auth_login(request: Request):
    if auth_limited.too_many(client_ip(request)):
        return _error("登录太频繁，稍后再试。", 429)
    body = await _read_object(request)
    provider = body.get("provider") if isinstance(body.get("provider"), str) else ""
    uid = body.get("uid") if isinstance(body.get("uid"), str) else ""
    password = body.get("password") if isinstance(body.get("password"), str) else ""
    return login_account(provider, uid, password, _platform(body), _device_id(body))


@app.post("/api/auth/logout")
def api_auth_logout(request: Request):
    revoke_session(bearer_token(request))
    return {"ok": True}


@app.get("/api/me")
def api_me(request: Request):
    user = require_user(request)
    return load_me(user["id"])


@app.patch("/api/me")
@app.put("/api/me")
async def api_me_update(request: Request):
    user = require_user(request)
    body = await _read_object(request)
    return update_me(user["id"], body)


@app.get("/api/progress")
def api_progress(request: Request):
    user = require_user(request)
    return load_progress(user["id"])


@app.put("/api/progress")
async def api_progress_put(request: Request):
    user = require_user(request)
    body = await _read_object(request)
    return merge_progress(user["id"], body)


@app.post("/api/practice")
async def api_practice(request: Request):
    user = require_user(request)
    body = await _read_object(request)
    question_id = body.get("questionId") if isinstance(body.get("questionId"), str) else ""
    answer = body.get("answerText") if isinstance(body.get("answerText"), str) else ""
    mode = body.get("inputMode") if isinstance(body.get("inputMode"), str) else "text"
    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    if not question_id:
        raise HTTPException(status_code=400, detail="需要 questionId。")
    add_practice(user["id"], user.get("deviceId") or None, question_id, answer, mode, result)
    return {"ok": True}


@app.get("/api/admin/stats")
def api_admin_stats(request: Request):
    require_admin(request)
    return admin_stats()


@app.get("/api/admin/topics")
def api_admin_topics(request: Request):
    require_admin(request)
    return {"items": list_topics()}


@app.get("/api/admin/questions")
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


@app.get("/api/admin/questions/{qid}")
def api_admin_question(request: Request, qid: str):
    require_admin(request)
    return get_question(qid)


@app.patch("/api/admin/questions/{qid}")
async def api_admin_patch_question(request: Request, qid: str):
    require_admin(request)
    body = await _read_object(request)
    return patch_question(qid, body)


@app.get("/api/admin/releases")
def api_admin_releases(request: Request):
    require_admin(request)
    return {"items": list_releases()}


@app.post("/api/admin/releases")
async def api_admin_publish(request: Request):
    require_admin(request)
    body = await _read_object(request)
    version = body.get("version") if isinstance(body.get("version"), str) else ""
    summary = body.get("summary") if isinstance(body.get("summary"), str) else ""
    return publish_release(version, summary)


@app.get("/admin")
def admin_page():
    if not ADMIN_HTML.is_file():
        raise HTTPException(status_code=404, detail="中台页面不存在。")
    return HTMLResponse(ADMIN_HTML.read_text(encoding="utf-8"))


@app.get("/api/judge")
def api_judge_status():
    return {"serverConfigured": bool(_server_llm()["apiKey"])}


@app.post("/api/judge")
async def api_judge(request: Request):
    user = require_user(request)
    if _ai_limited(judge_limited, judge_global, user):
        return _error("点评太频繁，稍等一分钟再试。", 429)
    try:
        body = await request.json()
    except Exception:
        return _error("请求体不是 JSON。", 400)
    if not isinstance(body, dict):
        return _error("请求体不是 JSON。", 400)

    user_answer = body.get("userAnswer").strip() if isinstance(body.get("userAnswer"), str) else ""
    if not user_answer:
        return _error("先写下你的答案。", 400)
    if not body.get("question") or not body.get("gist"):
        return _error("题目不完整。", 400)

    points = body.get("points") if isinstance(body.get("points"), list) else []
    points = [p for p in points if isinstance(p, dict)][:8]
    payload = {**body, "userAnswer": user_answer, "points": points}
    full = payload.get("fullAnswer")
    payload["fullAnswer"] = full[:2400] if isinstance(full, str) else ""

    body_key = body.get("apiKey").strip() if isinstance(body.get("apiKey"), str) else ""
    body_base = body.get("baseUrl") if isinstance(body.get("baseUrl"), str) else ""
    body_model = body.get("model") if isinstance(body.get("model"), str) else ""
    api_key, base_url, model_in = _provider_llm(body_key, body_base, body_model)
    provider = "qwen" if re.search(r"dashscope", base_url, re.I) else "custom"
    model = chat_model_for(base_url, model_in, provider)

    if not api_key:
        result = mock_judge(
            user_answer,
            str(body.get("gist") or ""),
            points,
            str(body.get("redFlags") or ""),
            str(body.get("bonus") or ""),
        )
        _record_practice(request, body, user_answer, result)
        return result

    try:
        url = completions_url(base_url)
        response = await async_http().post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "temperature": 0.3, "messages": build_judge_messages(payload)},
        )
        raw_text = response.text
        if looks_like_html(raw_text) or (response.status_code >= 400 and not raw_text.strip().startswith("{")):
            return _error("大模型接口返回异常，请检查接口地址或稍后重试。", 502)
        if response.status_code >= 400:
            return _error("大模型接口暂时不可用，请稍后重试。", 502)
        completion = parse_json_text(raw_text, "大模型接口")
        content = ""
        if isinstance(completion, dict):
            choices = completion.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                message = choices[0].get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    content = message["content"]
        result = normalize_judge_result(extract_json_object(content), "llm", model)
        _record_practice(request, body, user_answer, result)
        return result
    except HTTPException:
        raise
    except Exception as err:
        log_event("judge_error", error=str(err), requestId=getattr(request.state, "request_id", None))
        return _error("调用大模型失败", 502)


@app.post("/api/tts")
async def api_tts(request: Request):
    user = require_user(request)
    if _ai_limited(tts_limited, tts_global, user):
        return _error("朗读太频繁，稍等再听。", 429)
    try:
        body = await request.json()
    except Exception:
        return _error("请求体不是 JSON。", 400)
    if not isinstance(body, dict):
        return _error("请求体不是 JSON。", 400)
    text = body.get("text").strip() if isinstance(body.get("text"), str) else ""
    if not text:
        return _error("没有要朗读的文字。", 400)
    if len(text) > MAX_TTS_CHARS:
        return _error("这一段太长，请再拆短一点。", 400)

    body_key = body.get("apiKey").strip() if isinstance(body.get("apiKey"), str) else ""
    body_base = body.get("baseUrl") if isinstance(body.get("baseUrl"), str) else ""
    api_key, base_url, _model = _provider_llm(body_key, body_base, "")
    provider = (body.get("provider").strip() if body_key and isinstance(body.get("provider"), str) else "") or "qwen"
    role = "interviewer" if body.get("role") == "interviewer" else "coach"
    if not api_key:
        return JSONResponse({"error": "cloud-unconfigured", "fallback": True}, status_code=400)
    try:
        result = await asyncio.to_thread(synthesize_speech, api_key, base_url, provider, text, role)
        return Response(
            content=result["bytes"],
            media_type=result["mime"],
            headers={"X-Tts-Engine": str(result.get("engine") or ""), "Cache-Control": "no-store"},
        )
    except HTTPException:
        raise
    except Exception as err:
        log_event("tts_error", error=str(err), requestId=getattr(request.state, "request_id", None))
        return _error("语音合成失败", 502)


@app.post("/api/transcribe")
async def api_transcribe(request: Request):
    user = require_user(request)
    if _ai_limited(asr_limited, asr_global, user):
        return _error("听写太频繁，稍等一分钟再试。", 429)
    try:
        form = await request.form()
    except Exception:
        return _error("请用表单上传音频。", 400)

    audio = form.get("audio")
    if not isinstance(audio, UploadFile):
        return _error("没有收到录音。", 400)
    data = await audio.read()
    if not data:
        return _error("没有收到录音。", 400)
    if len(data) > MAX_AUDIO_BYTES:
        return _error("录音超过 8MB，说短一点或分段说。", 413)

    body_key = str(form.get("apiKey") or "").strip()
    body_base = str(form.get("baseUrl") or "")
    api_key, base_url, _model = _provider_llm(body_key, body_base, "")
    asr_provider = str(form.get("provider") or "qwen").strip() or "qwen"
    if not api_key:
        return _error(
            "云端听写需要 API Key。Chrome 可直接点麦克风用浏览器听写；国内网络不稳时，在右上角填通义 Key。",
            400,
        )

    raw_mime = (audio.content_type or str(form.get("mime") or "")).strip() or "audio/webm"
    meta = mime_to_audio_meta(raw_mime)
    filename = audio.filename.strip() if audio.filename and audio.filename.strip() else f"answer.{meta['ext']}"
    try:
        return await asyncio.to_thread(
            transcribe_audio, api_key, base_url, asr_provider, data, meta["mime"], filename
        )
    except HTTPException:
        raise
    except Exception as err:
        log_event("transcribe_error", error=str(err), requestId=getattr(request.state, "request_id", None))
        return _error("听写失败", 502)


def _resume_stored_name(filename: str) -> str:
    suffix = Path(Path(filename or "").name).suffix.lower()
    if suffix in {".pdf", ".docx", ".jpg", ".jpeg", ".png", ".webp"}:
        return f"upload{suffix}"
    return "upload.bin"


@app.post("/api/resumes")
async def api_resumes_upload(request: Request, background_tasks: BackgroundTasks):
    user = require_user(request)
    if resume_upload_limited.too_many(client_ip(request)):
        return _error("上传太频繁，稍等一分钟再试。", 429)
    try:
        form = await request.form()
    except Exception:
        return _error("请用表单上传简历。", 400)
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            return _error("没有收到文件。", 400)
        filename = upload.filename.strip() if upload.filename and upload.filename.strip() else "upload.bin"
        mime = (upload.content_type or "").strip()
        data = await upload.read()
    finally:
        await form.close()
    reason = reject_reason(filename, mime, len(data))
    if reason:
        return _error(reason, 400)
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


@app.get("/api/resumes")
def api_resumes_list(request: Request):
    user = require_user(request)
    with get_conn() as conn:
        return list_resumes(conn, user["id"])


@app.get("/api/resumes/{resume_id}")
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


@app.put("/api/resumes/{resume_id}")
async def api_resume_put(request: Request, resume_id: str):
    user = require_user(request)
    body = await _read_object(request)
    with get_conn() as conn:
        return replace_and_load(conn, user["id"], resume_id, body)


@app.post("/api/resumes/{resume_id}/confirm")
def api_resume_confirm(request: Request, resume_id: str):
    user = require_user(request)
    with get_conn() as conn:
        return confirm_resume(conn, user["id"], resume_id)


@app.post("/api/mock/sessions")
async def api_mock_sessions_create(request: Request):
    user = require_user(request)
    body = await _read_object(request)
    return create_session(user, body)


@app.post("/api/mock/sessions/{session_id}/turn")
async def api_mock_sessions_turn(request: Request, session_id: str):
    user = require_user(request)
    if resume_turn_limited.too_many(client_ip(request)):
        return _error("出题太频繁，稍等一分钟再试。", 429)
    body = await _read_object(request)
    return take_turn(user, session_id, body)


@app.post("/api/mock/sessions/{session_id}/finish")
def api_mock_sessions_finish(request: Request, session_id: str):
    user = require_user(request)
    return finish_session(user, session_id)


web_root = Path(WEB_ROOT) if WEB_ROOT else None
if web_root and web_root.is_dir():
    app.mount("/", StaticFiles(directory=web_root, html=True), name="web")
