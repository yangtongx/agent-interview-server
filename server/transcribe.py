from __future__ import annotations

import base64
import json
import re
from typing import Any

from server.config import QWEN_ASR_MODEL
from server.http_client import async_http
from server.judge import completions_url, looks_like_html, parse_json_text


def asr_engine(provider: str, base_url: str) -> str:
    if provider == "deepseek":
        return "unsupported"
    if provider == "qwen" or re.search(r"dashscope\.aliyuncs\.com", base_url, re.I):
        return "qwen"
    return "whisper"


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict):
            if isinstance(part.get("text"), str):
                parts.append(part["text"])
            elif isinstance(part.get("transcript"), str):
                parts.append(part["transcript"])
    return "".join(parts).strip()


def extract_transcript(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    if isinstance(payload.get("text"), str) and payload["text"].strip():
        return payload["text"].strip()
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        choice = choices[0]
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        from_message = _content_to_text(message.get("content") if isinstance(message, dict) else None)
        if from_message:
            return from_message
        if isinstance(choice.get("text"), str) and choice["text"].strip():
            return choice["text"].strip()
    output = payload.get("output")
    if isinstance(output, dict):
        if isinstance(output.get("text"), str) and output["text"].strip():
            return output["text"].strip()
        deeper = extract_transcript(output)
        if deeper:
            return deeper
    return ""


def _upstream_error(status: int, raw_text: str) -> str:
    if looks_like_html(raw_text):
        return f"语音接口 HTTP {status} 返回了网页。通义请用 https://dashscope.aliyuncs.com/compatible-mode/v1 。"
    try:
        parsed = json.loads(raw_text)
        if isinstance(parsed, dict):
            error = parsed.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                return error["message"]
            if isinstance(parsed.get("message"), str):
                return parsed["message"]
    except json.JSONDecodeError:
        pass
    return raw_text[:280] or f"HTTP {status}"


def _data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


async def _transcribe_qwen(api_key: str, base_url: str, data: bytes, mime: str) -> str:
    response = await async_http().post(
        completions_url(base_url),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": QWEN_ASR_MODEL,
            "stream": False,
            "asr_options": {"enable_itn": True},
            "messages": [
                {
                    "role": "system",
                    "content": "中文技术面试口述。专有名词：Agent、LangGraph、RAG、MCP、SSE、Tool Call、意图识别、编排、记忆。",
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": _data_uri(data, mime)},
                        }
                    ],
                },
            ],
        },
    )
    raw_text = response.text
    if response.status_code >= 400:
        raise RuntimeError(f"通义听写 {response.status_code}：{_upstream_error(response.status_code, raw_text)}")
    payload = parse_json_text(raw_text, "通义听写")
    text = extract_transcript(payload)
    if not text:
        raise RuntimeError("通义听写没有返回文字。再说一遍，或换一段更清晰的录音。")
    return text


def _transcriptions_url(base_url: str) -> str:
    trimmed = base_url.strip().rstrip("/")
    root = re.sub(r"/chat/completions$", "", trimmed)
    if root.endswith("/audio/transcriptions"):
        return root
    return f"{root}/audio/transcriptions"


async def _transcribe_whisper(api_key: str, base_url: str, data: bytes, mime: str, filename: str) -> str:
    response = await async_http().post(
        _transcriptions_url(base_url),
        headers={"Authorization": f"Bearer {api_key}"},
        files={"file": (filename, data, mime)},
        data={"model": "whisper-1", "language": "zh", "response_format": "json"},
    )
    raw_text = response.text
    if response.status_code >= 400:
        raise RuntimeError(f"Whisper 听写 {response.status_code}：{_upstream_error(response.status_code, raw_text)}")
    payload = parse_json_text(raw_text, "Whisper 听写")
    text = extract_transcript(payload)
    if not text:
        raise RuntimeError("Whisper 没有返回文字。")
    return text


async def transcribe_audio(
    api_key: str,
    base_url: str,
    provider: str,
    data: bytes,
    mime: str,
    filename: str,
) -> dict[str, str]:
    engine = asr_engine(provider, base_url)
    if engine == "unsupported":
        raise RuntimeError("DeepSeek 没有语音识别。请改用通义，或用 Chrome 的浏览器听写（不需要 Key）。")
    if engine == "qwen":
        return {"text": await _transcribe_qwen(api_key, base_url, data, mime), "engine": QWEN_ASR_MODEL}
    try:
        return {
            "text": await _transcribe_whisper(api_key, base_url, data, mime, filename),
            "engine": "whisper-1",
        }
    except Exception:
        if re.search(r"dashscope", base_url, re.I):
            return {"text": await _transcribe_qwen(api_key, base_url, data, mime), "engine": QWEN_ASR_MODEL}
        raise
