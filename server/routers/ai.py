from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.datastructures import UploadFile

from server.auth import require_user
from server.config import MAX_AUDIO_BYTES, MAX_TTS_CHARS
from server.deps import (
    ai_limited,
    asr_global,
    asr_limited,
    error,
    judge_global,
    judge_limited,
    provider_llm,
    record_practice,
    server_llm,
    tts_global,
    tts_limited,
)
from server.http_client import async_http
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
from server.observability import log_event
from server.speech import mime_to_audio_meta
from server.transcribe import transcribe_audio
from server.tts import synthesize_speech

router = APIRouter()


@router.get("/api/judge")
def api_judge_status():
    return {"serverConfigured": bool(server_llm()["apiKey"])}


@router.post("/api/judge")
async def api_judge(request: Request):
    user = require_user(request)
    if ai_limited(judge_limited, judge_global, user):
        return error("点评太频繁，稍等一分钟再试。", 429)
    try:
        body = await request.json()
    except Exception:
        return error("请求体不是 JSON。", 400)
    if not isinstance(body, dict):
        return error("请求体不是 JSON。", 400)

    user_answer = body.get("userAnswer").strip() if isinstance(body.get("userAnswer"), str) else ""
    if not user_answer:
        return error("先写下你的答案。", 400)
    if not body.get("question") or not body.get("gist"):
        return error("题目不完整。", 400)

    points = body.get("points") if isinstance(body.get("points"), list) else []
    points = [p for p in points if isinstance(p, dict)][:8]
    payload = {**body, "userAnswer": user_answer, "points": points}
    full = payload.get("fullAnswer")
    payload["fullAnswer"] = full[:2400] if isinstance(full, str) else ""

    body_key = body.get("apiKey").strip() if isinstance(body.get("apiKey"), str) else ""
    body_base = body.get("baseUrl") if isinstance(body.get("baseUrl"), str) else ""
    body_model = body.get("model") if isinstance(body.get("model"), str) else ""
    api_key, base_url, model_in = provider_llm(body_key, body_base, body_model)
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
        record_practice(request, body, user_answer, result)
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
            return error("大模型接口返回异常，请检查接口地址或稍后重试。", 502)
        if response.status_code >= 400:
            return error("大模型接口暂时不可用，请稍后重试。", 502)
        completion = parse_json_text(raw_text, "大模型接口")
        content = ""
        if isinstance(completion, dict):
            choices = completion.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                message = choices[0].get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    content = message["content"]
        result = normalize_judge_result(extract_json_object(content), "llm", model)
        record_practice(request, body, user_answer, result)
        return result
    except HTTPException:
        raise
    except Exception as err:
        log_event("judge_error", error=str(err), requestId=getattr(request.state, "request_id", None))
        return error("调用大模型失败", 502)


@router.post("/api/tts")
async def api_tts(request: Request):
    user = require_user(request)
    if ai_limited(tts_limited, tts_global, user):
        return error("朗读太频繁，稍等再听。", 429)
    try:
        body = await request.json()
    except Exception:
        return error("请求体不是 JSON。", 400)
    if not isinstance(body, dict):
        return error("请求体不是 JSON。", 400)
    text = body.get("text").strip() if isinstance(body.get("text"), str) else ""
    if not text:
        return error("没有要朗读的文字。", 400)
    if len(text) > MAX_TTS_CHARS:
        return error("这一段太长，请再拆短一点。", 400)

    body_key = body.get("apiKey").strip() if isinstance(body.get("apiKey"), str) else ""
    body_base = body.get("baseUrl") if isinstance(body.get("baseUrl"), str) else ""
    api_key, base_url, _model = provider_llm(body_key, body_base, "")
    provider = (body.get("provider").strip() if body_key and isinstance(body.get("provider"), str) else "") or "qwen"
    role = "interviewer" if body.get("role") == "interviewer" else "coach"
    if not api_key:
        return JSONResponse({"error": "cloud-unconfigured", "fallback": True}, status_code=400)
    try:
        result = await synthesize_speech(api_key, base_url, provider, text, role)
        return Response(
            content=result["bytes"],
            media_type=result["mime"],
            headers={"X-Tts-Engine": str(result.get("engine") or ""), "Cache-Control": "no-store"},
        )
    except HTTPException:
        raise
    except Exception as err:
        log_event("tts_error", error=str(err), requestId=getattr(request.state, "request_id", None))
        return error("语音合成失败", 502)


@router.post("/api/transcribe")
async def api_transcribe(request: Request):
    user = require_user(request)
    if ai_limited(asr_limited, asr_global, user):
        return error("听写太频繁，稍等一分钟再试。", 429)
    try:
        form = await request.form()
    except Exception:
        return error("请用表单上传音频。", 400)

    audio = form.get("audio")
    if not isinstance(audio, UploadFile):
        return error("没有收到录音。", 400)
    data = await audio.read()
    if not data:
        return error("没有收到录音。", 400)
    if len(data) > MAX_AUDIO_BYTES:
        return error("录音超过 8MB，说短一点或分段说。", 413)

    body_key = str(form.get("apiKey") or "").strip()
    body_base = str(form.get("baseUrl") or "")
    api_key, base_url, _model = provider_llm(body_key, body_base, "")
    asr_provider = str(form.get("provider") or "qwen").strip() or "qwen"
    if not api_key:
        return error(
            "云端听写需要 API Key。Chrome 可直接点麦克风用浏览器听写；国内网络不稳时，在右上角填通义 Key。",
            400,
        )

    raw_mime = (audio.content_type or str(form.get("mime") or "")).strip() or "audio/webm"
    meta = mime_to_audio_meta(raw_mime)
    filename = audio.filename.strip() if audio.filename and audio.filename.strip() else f"answer.{meta['ext']}"
    try:
        return await transcribe_audio(api_key, base_url, asr_provider, data, meta["mime"], filename)
    except HTTPException:
        raise
    except Exception as err:
        log_event("transcribe_error", error=str(err), requestId=getattr(request.state, "request_id", None))
        return error("听写失败", 502)
