"""Application settings. Secrets come from the environment, never from source."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


PG_URI = _env("PG_URI")
LLM_API_KEY = _env("LLM_API_KEY") or _env("DASHSCOPE_API_KEY")
LLM_BASE_URL = _env("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
LLM_MODEL = _env("LLM_MODEL", "qwen-plus")
HOST = _env("HOST", "0.0.0.0")
PORT = int(_env("PORT", "43180") or "43180")
DATA_DIR = _env("QUESTION_DATA_DIR") or str(ROOT / "data")


def resolve_web_root() -> str:
    explicit = _env("WEB_ROOT")
    if explicit:
        return explicit
    out = ROOT / "out"
    return str(out) if out.is_dir() else ""


WEB_ROOT = resolve_web_root()
ADMIN_TOKEN = _env("ADMIN_TOKEN")
SESSION_DAYS = int(_env("SESSION_DAYS", "30") or "30")
MAX_TTS_CHARS = 560
MAX_AUDIO_BYTES = 8 * 1024 * 1024
RESUME_MAX_BYTES = 8 * 1024 * 1024
RESUME_FILE_KEY = _env("RESUME_FILE_KEY")
APP_ENV = _env("APP_ENV", "development").lower()
TRUSTED_PROXY_IPS = frozenset(part.strip() for part in _env("TRUSTED_PROXY_IPS").split(",") if part.strip())
LLM_ALLOWED_HOSTS = frozenset(
    part.strip().lower()
    for part in _env(
        "LLM_ALLOWED_HOSTS",
        "dashscope.aliyuncs.com,api.deepseek.com,api.openai.com",
    ).split(",")
    if part.strip()
)


def _flag(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


REQUIRE_RESUME_ENCRYPTION = _flag("REQUIRE_RESUME_ENCRYPTION", APP_ENV in ("production", "prod"))
QWEN_ASR_MODEL = "qwen3-asr-flash"
QWEN_TTS_INSTRUCT = "qwen3-tts-instruct-flash"
QWEN_TTS_FLASH = "qwen3-tts-flash"
DASHSCOPE_NATIVE_TTS = (
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
)
