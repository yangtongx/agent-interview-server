from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable
from typing import Any, Literal

from server.config import DASHSCOPE_NATIVE_TTS, MAX_TTS_CHARS, QWEN_TTS_FLASH, QWEN_TTS_INSTRUCT
from server.egress import UnsafeUrl, validate_tts_media_url
from server.http_client import http
from server.judge import looks_like_html, parse_json_text

SpeakRole = Literal["interviewer", "coach"]
AudioResult = dict[str, Any]
Strategy = Callable[[str, str, str, SpeakRole], AudioResult]

TTS_INSTRUCTIONS: dict[SpeakRole, str] = {
    "interviewer": (
        "中文普通话。你是阿里的技术面试官，坐在对面提问。语气沉稳、有探究感，句子有起伏，关键词略加重。"
        "像真人说话，不要播音腔，不要机械念稿。语速中等偏慢。"
    ),
    "coach": (
        "中文普通话。你是刚面完的学长，在耳机里给同事讲怎么答。语气松弛、有来有回，像聊天。"
        "重点放慢、加重，举例时更口语。不要念稿，不要播音腔。"
    ),
}

_cached: Strategy | None = None


def tts_voice(role: SpeakRole) -> str:
    return "Ethan" if role == "interviewer" else "Cherry"


def is_dashscope_base(base_url: str) -> bool:
    return bool(re.search(r"dashscope\.aliyuncs\.com", base_url, re.I) or re.search(r"qwen", base_url, re.I))


def speech_url(base_url: str) -> str:
    trimmed = base_url.strip().rstrip("/")
    root = re.sub(r"/chat/completions$", "", trimmed)
    if root.endswith("/audio/speech"):
        return root
    return f"{root}/audio/speech"


def audio_url_candidates(url: str) -> list[str]:
    trimmed = url.strip()
    if not trimmed:
        return []
    if trimmed.startswith("http://"):
        trimmed = "https://" + trimmed[len("http://") :]
    if not trimmed.startswith("https://"):
        return []
    return [trimmed]


def sniff_audio_mime(data: bytes) -> str:
    if len(data) >= 12 and data[0:4] == b"RIFF":
        return "audio/wav"
    if len(data) >= 3 and data[0:3] == b"ID3":
        return "audio/mpeg"
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return "audio/mpeg"
    if len(data) >= 4 and data[0:4] == b"OggS":
        return "audio/ogg"
    return "audio/wav"


def qwen_business_error(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    status = payload.get("status_code") if isinstance(payload.get("status_code"), int) else 200
    code = payload.get("code").strip() if isinstance(payload.get("code"), str) else ""
    message = payload.get("message").strip() if isinstance(payload.get("message"), str) else ""
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    nested = ""
    if isinstance(error.get("message"), str) and error["message"].strip():
        nested = error["message"].strip()
    elif isinstance(error.get("code"), str) and error["code"].strip():
        nested = error["code"].strip()
    if status >= 400:
        return message or nested or code or f"status_code {status}"
    if code and not re.match(r"^(success|ok|null)?$", code, re.I):
        return message or nested or code
    if nested:
        return nested
    return None


def explain_tts_failure(raw: str) -> str:
    text = raw.strip()
    if re.search(r"Arrearage|quota|余额|欠费|insufficient", text, re.I):
        return f"百炼余额不足：{text}"
    if re.search(r"InvalidApiKey|Unauthorized|invalid.*api.?key|401", text, re.I):
        return f"API Key 无效：{text}"
    if re.search(r"AccessDenied|no permission|没有权限|not authorized|Model\.AccessDenied", text, re.I):
        return f"这个 Key 还没开通语音模型。打开百炼控制台开通 qwen3-tts-flash / qwen3-tts-instruct-flash。{text}"
    if re.search(r"not found|does not exist|InvalidModel|不存在|invalid model", text, re.I):
        return f"语音模型未开通或不存在。请在百炼开通 qwen3-tts-flash。{text}"
    return text


def build_qwen_native_bodies(text: str, role: SpeakRole, model: str) -> list[dict[str, Any]]:
    voice = tts_voice(role)
    payload_input: dict[str, Any] = {"text": text, "voice": voice, "language_type": "Chinese"}
    if "instruct" not in model:
        return [{"model": model, "input": payload_input}]
    instructions = TTS_INSTRUCTIONS[role]
    return [
        {
            "model": model,
            "input": {**payload_input, "instructions": instructions, "optimize_instructions": True},
        },
        {
            "model": model,
            "input": payload_input,
            "parameters": {"instructions": instructions, "optimize_instructions": True},
        },
        {"model": model, "input": payload_input},
    ]


def _upstream_error(status: int, raw_text: str) -> str:
    if looks_like_html(raw_text):
        return f"语音合成 HTTP {status} 返回了网页。"
    try:
        parsed = json.loads(raw_text)
        biz = qwen_business_error(parsed)
        if biz:
            return biz
        if isinstance(parsed, dict):
            error = parsed.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                return error["message"]
            if isinstance(parsed.get("message"), str):
                return parsed["message"]
    except json.JSONDecodeError:
        pass
    return raw_text[:280] or f"HTTP {status}"


def _download_audio(url: str) -> dict[str, Any]:
    last = "下载合成音频失败"
    for candidate in audio_url_candidates(url):
        try:
            safe = validate_tts_media_url(candidate)
        except UnsafeUrl:
            last = "音频地址不被允许"
            continue
        response = http().get(safe)
        if response.status_code >= 400:
            last = f"下载合成音频失败 HTTP {response.status_code}"
            continue
        data = response.content
        if len(data) < 64:
            last = "下载到的音频是空的"
            continue
        header_type = (response.headers.get("content-type") or "").split(";")[0]
        mime = header_type if header_type.startswith("audio/") else sniff_audio_mime(data)
        return {"bytes": data, "mime": mime}
    raise RuntimeError(last)


def _pick_audio_node(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    raw = output.get("audio") if isinstance(output, dict) else None
    if raw is None:
        raw = payload.get("audio")
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        return raw[0]
    if isinstance(raw, dict):
        return raw
    return None


def _bytes_from_qwen_payload(payload: Any) -> dict[str, Any]:
    biz = qwen_business_error(payload)
    if biz:
        raise RuntimeError(biz)
    audio = _pick_audio_node(payload)
    data = audio.get("data") if audio else None
    url = audio.get("url") if audio else None
    if isinstance(data, str) and data.strip():
        raw = base64.b64decode(data)
        if len(raw) < 64:
            raise RuntimeError("通义语音返回的音频是空的。")
        return {"bytes": raw, "mime": sniff_audio_mime(raw)}
    if isinstance(url, str) and url.strip():
        return _download_audio(url)
    raise RuntimeError("通义语音没有返回音频。请确认百炼已开通 qwen3-tts-flash。")


def _synthesize_qwen_native(api_key: str, base_url: str, text: str, role: SpeakRole, model: str) -> AudioResult:
    if not is_dashscope_base(base_url):
        raise RuntimeError("当前接口不是百炼，无法走通义语音。")
    errors: list[str] = []
    for body in build_qwen_native_bodies(text, role, model):
        response = http().post(
            DASHSCOPE_NATIVE_TTS,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
        )
        raw_text = response.text
        if response.status_code >= 400:
            errors.append(_upstream_error(response.status_code, raw_text))
            continue
        try:
            payload = parse_json_text(raw_text, "通义语音")
            audio = _bytes_from_qwen_payload(payload)
            return {**audio, "engine": model}
        except Exception as exc:
            errors.append(str(exc) or "通义语音失败")
    raise RuntimeError(errors[-1] if errors else "通义语音失败")


def _synthesize_compatible(api_key: str, base_url: str, text: str, role: SpeakRole, model: str) -> AudioResult:
    body: dict[str, Any] = {
        "model": model,
        "input": text,
        "voice": tts_voice(role),
        "language_type": "Chinese",
        "stream": False,
        "response_format": "wav",
    }
    if "instruct" in model:
        body["instructions"] = TTS_INSTRUCTIONS[role]
    response = http().post(
        speech_url(base_url),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body,
    )
    content_type = response.headers.get("content-type") or ""
    if response.status_code >= 400 or "application/json" in content_type:
        raise RuntimeError(_upstream_error(response.status_code, response.text))
    data = response.content
    if len(data) < 64:
        raise RuntimeError("兼容模式语音返回的音频是空的。")
    return {"bytes": data, "mime": sniff_audio_mime(data), "engine": model}


def _synthesize_openai(api_key: str, base_url: str, text: str, role: SpeakRole) -> AudioResult:
    response = http().post(
        speech_url(base_url),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": "gpt-4o-mini-tts",
            "voice": "ash" if role == "interviewer" else "coral",
            "input": text,
            "instructions": TTS_INSTRUCTIONS[role],
            "response_format": "mp3",
        },
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"OpenAI 语音 {response.status_code}：{_upstream_error(response.status_code, response.text)}"
        )
    data = response.content
    if len(data) < 64:
        raise RuntimeError("OpenAI 语音返回的音频是空的。")
    mime = (response.headers.get("content-type") or "").split(";")[0] or "audio/mpeg"
    return {"bytes": data, "mime": mime, "engine": "gpt-4o-mini-tts"}


def _qwen_strategies() -> list[tuple[str, Strategy]]:
    return [
        (
            "qwen-native-instruct",
            lambda api_key, base_url, text, role: _synthesize_qwen_native(
                api_key, base_url, text, role, QWEN_TTS_INSTRUCT
            ),
        ),
        (
            "qwen-native-flash",
            lambda api_key, base_url, text, role: _synthesize_qwen_native(
                api_key, base_url, text, role, QWEN_TTS_FLASH
            ),
        ),
        (
            "qwen-compat-instruct",
            lambda api_key, base_url, text, role: _synthesize_compatible(
                api_key, base_url, text, role, QWEN_TTS_INSTRUCT
            ),
        ),
        (
            "qwen-compat-flash",
            lambda api_key, base_url, text, role: _synthesize_compatible(
                api_key, base_url, text, role, QWEN_TTS_FLASH
            ),
        ),
    ]


def synthesize_speech(api_key: str, base_url: str, provider: str, text: str, role: SpeakRole) -> AudioResult:
    global _cached
    text = text.strip()
    if not text:
        raise RuntimeError("没有要朗读的内容。")
    if len(text) > MAX_TTS_CHARS:
        raise RuntimeError("单段朗读过长，请分段。")
    if provider == "deepseek":
        raise RuntimeError("DeepSeek 没有语音合成。请改用通义，或先听浏览器朗读。")

    use_qwen = provider == "qwen" or is_dashscope_base(base_url)
    if not use_qwen:
        return _synthesize_openai(api_key, base_url, text, role)

    if _cached is not None:
        try:
            return _cached(api_key, base_url, text, role)
        except Exception:
            _cached = None

    errors: list[str] = []
    for _name, run in _qwen_strategies():
        try:
            result = run(api_key, base_url, text, role)
            _cached = run
            return result
        except Exception as exc:
            errors.append(str(exc) or _name)
    raise RuntimeError(explain_tts_failure(errors[-1] if errors else "通义语音失败"))
