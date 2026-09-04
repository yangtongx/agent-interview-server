from __future__ import annotations


def mime_to_audio_meta(mime: str) -> dict[str, str]:
    base = (mime.split(";")[0] if mime else "").strip().lower() or "application/octet-stream"
    if base == "audio/webm":
        return {"mime": base, "ext": "webm"}
    if base in ("audio/mp4", "video/mp4"):
        return {"mime": "audio/mp4", "ext": "m4a"}
    if base == "audio/mpeg":
        return {"mime": base, "ext": "mp3"}
    if base in ("audio/wav", "audio/x-wav", "audio/wave"):
        return {"mime": "audio/wav", "ext": "wav"}
    if base in ("audio/pcm", "audio/l16", "audio/raw"):
        return {"mime": "audio/wav", "ext": "wav"}
    if base == "audio/x-m4a":
        return {"mime": "audio/mp4", "ext": "m4a"}
    if base.startswith("audio/ogg"):
        return {"mime": "audio/ogg", "ext": "ogg"}
    if base == "audio/aac":
        return {"mime": base, "ext": "aac"}
    return {"mime": base, "ext": "webm"}
