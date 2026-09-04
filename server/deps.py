from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from server.auth import optional_user
from server.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from server.egress import UnsafeUrl, validate_provider_url
from server.observability import log_event
from server.progress_sync import add_practice
from server.rate_limit import RateLimiter, client_ip

judge_limited = RateLimiter(20)
tts_limited = RateLimiter(120)
asr_limited = RateLimiter(10)
judge_global = RateLimiter(300)
tts_global = RateLimiter(600)
asr_global = RateLimiter(120)
auth_limited = RateLimiter(40)
resume_upload_limited = RateLimiter(10)
resume_turn_limited = RateLimiter(20)


def server_llm() -> dict[str, str]:
    return {"apiKey": LLM_API_KEY, "baseUrl": LLM_BASE_URL, "model": LLM_MODEL}


def ai_limited(user_limiter: RateLimiter, global_limiter: RateLimiter, user: dict[str, Any]) -> bool:
    return user_limiter.too_many(f"user:{user['id']}") or global_limiter.too_many("all")


def provider_llm(api_key_in: str, base_url_in: str, model_in: str) -> tuple[str, str, str]:
    server = server_llm()
    body_key = api_key_in.strip()
    api_key = body_key or server["apiKey"]
    raw_base = base_url_in.strip() if body_key and base_url_in.strip() else server["baseUrl"]
    try:
        base_url = validate_provider_url(raw_base)
    except UnsafeUrl as err:
        raise HTTPException(status_code=400, detail="接口地址不被允许。") from err
    requested_model = model_in.strip() if body_key and model_in.strip() else ""
    return api_key, base_url, requested_model or server["model"]


def error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


async def read_object(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体不是 JSON。")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体不是 JSON。")
    return body


def platform(body: dict[str, Any]) -> str:
    value = body.get("platform")
    return value if value in ("harmony", "web", "android", "ios") else "web"


def device_id(body: dict[str, Any]) -> str | None:
    value = body.get("deviceId")
    return value.strip() if isinstance(value, str) and value.strip() else None


def audience(body: dict[str, Any]) -> str:
    value = body.get("audience")
    return value if value in ("unset", "transition", "experienced") else "unset"


def record_practice(request: Request, body: dict[str, Any], user_answer: str, result: dict[str, Any]) -> None:
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


__all__ = [
    "ai_limited",
    "asr_global",
    "asr_limited",
    "audience",
    "auth_limited",
    "client_ip",
    "device_id",
    "error",
    "judge_global",
    "judge_limited",
    "platform",
    "provider_llm",
    "read_object",
    "record_practice",
    "resume_turn_limited",
    "resume_upload_limited",
    "server_llm",
    "tts_global",
    "tts_limited",
]
