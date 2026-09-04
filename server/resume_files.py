from __future__ import annotations

import hashlib
from base64 import urlsafe_b64encode
from pathlib import Path

from server.config import DATA_DIR, REQUIRE_RESUME_ENCRYPTION, RESUME_FILE_KEY, RESUME_MAX_BYTES

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class ResumeStorageError(RuntimeError):
    pass


def assert_resume_storage_ready() -> None:
    if REQUIRE_RESUME_ENCRYPTION and not RESUME_FILE_KEY:
        raise ResumeStorageError("生产环境必须配置 RESUME_FILE_KEY。")


def file_kind(filename: str, mime: str) -> str:
    name = (filename or "").lower().strip()
    mime = (mime or "").split(";")[0].strip().lower()
    if name.endswith(".doc") and not name.endswith(".docx"):
        return "doc"
    if name.endswith(".docx") or mime == DOCX_MIME:
        return "docx"
    if name.endswith(".pdf") or mime == "application/pdf":
        return "pdf"
    if name.endswith((".jpg", ".jpeg")) or mime in ("image/jpeg", "image/jpg"):
        return "jpeg"
    if name.endswith(".png") or mime == "image/png":
        return "png"
    if name.endswith(".webp") or mime == "image/webp":
        return "webp"
    if mime == "application/msword":
        return "doc"
    return "unknown"


def reject_reason(filename: str, mime: str, size: int) -> str | None:
    if size > RESUME_MAX_BYTES:
        return "文件超过 8MB，请压缩或改用 PDF。"
    kind = file_kind(filename, mime)
    if kind == "doc":
        return "旧版 .doc 不支持，请另存为 .docx 或 PDF。"
    if kind == "unknown":
        return "只支持 PDF、Word（.docx）和 jpeg / png / webp 图片。"
    return None


def resume_dir(user_id: str, resume_id: str) -> Path:
    path = Path(DATA_DIR) / "resumes" / str(user_id) / str(resume_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _fernet():
    if not RESUME_FILE_KEY:
        return None
    from cryptography.fernet import Fernet

    digest = hashlib.sha256(RESUME_FILE_KEY.encode("utf-8")).digest()
    return Fernet(urlsafe_b64encode(digest))


def write_stored(path: Path, data: bytes) -> None:
    if REQUIRE_RESUME_ENCRYPTION and not RESUME_FILE_KEY:
        raise ResumeStorageError("生产环境必须配置 RESUME_FILE_KEY。")
    path.parent.mkdir(parents=True, exist_ok=True)
    token = _fernet()
    path.write_bytes(token.encrypt(data) if token else data)


def read_stored(path: Path) -> bytes:
    data = path.read_bytes()
    token = _fernet()
    if not token:
        if REQUIRE_RESUME_ENCRYPTION:
            raise ResumeStorageError("生产环境必须配置 RESUME_FILE_KEY。")
        return data
    from cryptography.fernet import InvalidToken

    try:
        return token.decrypt(data)
    except InvalidToken:
        raise
