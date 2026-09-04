from __future__ import annotations

import io
from typing import Any

from server.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from server.http_client import http
from server.judge import completions_url, extract_json_object, looks_like_html

STRUCTURE_SYSTEM = (
    "你是简历结构化抽取器。只根据原文抽取，不要发明经历。只返回 JSON，不要 markdown。"
    "JSON 形状必须是："
    '{"profile":{"fullName":"","yearsExperience":null,"currentTitle":"","targetTitle":"",'
    '"summary":"","skills":[]},'
    '"jobs":[{"company":"","jobTitle":"","startedOn":null,"endedOn":null,"isCurrent":false,"summary":""}],'
    '"projects":[{"name":"","company":"","jobRole":"","startedOn":null,"endedOn":null,'
    '"techStack":[],"responsibilities":"","outcomes":"","jobIndex":null}]}'
)


def classify_parse(raw_text: str, projects: list[dict[str, Any]]) -> str:
    if not (raw_text or "").strip():
        return "failed"
    if not projects:
        return "needs_review"
    for project in projects:
        if not isinstance(project, dict):
            return "needs_review"
        name = str(project.get("name") or "").strip()
        responsibilities = str(project.get("responsibilities") or "").strip()
        if not name or not responsibilities:
            return "needs_review"
    return "parsed"


def extract_pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        parts: list[str] = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        return "\n".join(parts).strip()
    except Exception:
        return ""


def extract_docx_text(data: bytes) -> str:
    try:
        from docx import Document

        document = Document(io.BytesIO(data))
        return "\n".join(p.text for p in document.paragraphs if p.text).strip()
    except Exception:
        return ""


def _vision_ocr(data: bytes, mime: str) -> str:
    if not LLM_API_KEY:
        return ""
    import base64

    try:
        url = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
        response = http().post(
            completions_url(LLM_BASE_URL),
            headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "qwen-vl-plus",
                "temperature": 0,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": url}},
                            {"type": "text", "text": "请原样抄出图片里的全部文字，不要总结。只返回纯文本。"},
                        ],
                    }
                ],
            },
            timeout=60.0,
        )
        if response.status_code >= 400 or looks_like_html(response.text):
            return ""
        payload = response.json()
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else {}
            content = message.get("content") if isinstance(message, dict) else ""
            return content.strip() if isinstance(content, str) else ""
        return ""
    except Exception:
        return ""


def extract_image_text(data: bytes, mime: str) -> str:
    return (_vision_ocr(data, mime) or "").strip()


def extract_text(kind: str, data: bytes, mime: str) -> str:
    if kind == "pdf":
        return extract_pdf_text(data)
    if kind == "docx":
        return extract_docx_text(data)
    if kind in ("jpeg", "png", "webp"):
        mapped = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[kind]
        return extract_image_text(data, mime or mapped)
    return ""


def _as_str(value: Any, limit: int = 4000) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _as_skills(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip() and item.strip() not in out:
            out.append(item.strip()[:40])
        if len(out) >= 24:
            break
    return out


def _as_year(value: Any) -> int | None:
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    if year < 0 or year > 60:
        return None
    return year


def _as_date(value: Any) -> str | None:
    text = _as_str(value, 10)
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return None


def normalize_structured(raw: Any) -> dict[str, Any]:
    obj = raw if isinstance(raw, dict) else {}
    profile_in = obj.get("profile") if isinstance(obj.get("profile"), dict) else {}
    jobs_in = obj.get("jobs") if isinstance(obj.get("jobs"), list) else []
    projects_in = obj.get("projects") if isinstance(obj.get("projects"), list) else []
    profile = {
        "full_name": _as_str(profile_in.get("fullName") or profile_in.get("full_name"), 80),
        "years_experience": _as_year(profile_in.get("yearsExperience") or profile_in.get("years_experience")),
        "current_title": _as_str(profile_in.get("currentTitle") or profile_in.get("current_title"), 80) or None,
        "target_title": _as_str(profile_in.get("targetTitle") or profile_in.get("target_title"), 80) or None,
        "summary": _as_str(profile_in.get("summary"), 2000),
        "skills": _as_skills(profile_in.get("skills")),
    }
    jobs: list[dict[str, Any]] = []
    for i, row in enumerate(jobs_in[:12]):
        if not isinstance(row, dict):
            continue
        jobs.append(
            {
                "company": _as_str(row.get("company"), 80),
                "job_title": _as_str(row.get("jobTitle") or row.get("job_title"), 80),
                "started_on": _as_date(row.get("startedOn") or row.get("started_on")),
                "ended_on": _as_date(row.get("endedOn") or row.get("ended_on")),
                "is_current": bool(row.get("isCurrent") if "isCurrent" in row else row.get("is_current")),
                "summary": _as_str(row.get("summary"), 2000),
                "sort_order": i,
            }
        )
    projects: list[dict[str, Any]] = []
    for i, row in enumerate(projects_in[:16]):
        if not isinstance(row, dict):
            continue
        job_index = row.get("jobIndex") if row.get("jobIndex") is not None else row.get("_job_index")
        try:
            job_index_i = int(job_index) if job_index is not None else None
        except (TypeError, ValueError):
            job_index_i = None
        projects.append(
            {
                "name": _as_str(row.get("name"), 120),
                "company": _as_str(row.get("company"), 80) or None,
                "job_role": _as_str(row.get("jobRole") or row.get("job_role"), 80) or None,
                "started_on": _as_date(row.get("startedOn") or row.get("started_on")),
                "ended_on": _as_date(row.get("endedOn") or row.get("ended_on")),
                "is_current": bool(row.get("isCurrent") if "isCurrent" in row else row.get("is_current")),
                "tech_stack": _as_skills(row.get("techStack") or row.get("tech_stack")),
                "responsibilities": _as_str(row.get("responsibilities"), 4000),
                "outcomes": _as_str(row.get("outcomes"), 2000),
                "sort_order": i,
                "_job_index": job_index_i if job_index_i is not None and 0 <= job_index_i < len(jobs) else None,
            }
        )
    return {"profile": profile, "jobs": jobs, "projects": projects}


def empty_archive() -> dict[str, Any]:
    return normalize_structured({})


def structure_from_text(raw_text: str) -> dict[str, Any]:
    if not LLM_API_KEY:
        return empty_archive()
    try:
        response = http().post(
            completions_url(LLM_BASE_URL),
            headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": LLM_MODEL,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": STRUCTURE_SYSTEM},
                    {"role": "user", "content": (raw_text or "")[:12000]},
                ],
            },
            timeout=60.0,
        )
        if response.status_code >= 400 or looks_like_html(response.text):
            return empty_archive()
        payload = response.json()
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not (isinstance(choices, list) and choices):
            return empty_archive()
        message = choices[0].get("message") if isinstance(choices[0], dict) else {}
        content = message.get("content") if isinstance(message, dict) else ""
        if not isinstance(content, str) or not content.strip():
            return empty_archive()
        return normalize_structured(extract_json_object(content))
    except Exception:
        return empty_archive()


def run_parse_pipeline(kind: str, data: bytes, mime: str) -> dict[str, Any]:
    raw_text = extract_text(kind, data, mime)
    if not raw_text.strip():
        return {
            "raw_text": raw_text,
            "archive": empty_archive(),
            "status": "failed",
            "error": "没有从文件里抽出文字，请手填档案或换一份文件。",
        }
    archive = structure_from_text(raw_text)
    status = classify_parse(raw_text, archive["projects"])
    error = "档案不完整，请补全项目名和职责后再确认。" if status == "needs_review" else None
    return {"raw_text": raw_text, "archive": archive, "status": status, "error": error}
