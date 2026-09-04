from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from server.db import get_conn
from server.observability import log_event
from server.resume_files import file_kind, read_stored
from server.resume_parse import normalize_structured, run_parse_pipeline

CONFIRM_ERROR = "至少保留一个项目，并填写项目名和职责。"
NOT_FOUND = "简历不存在。"


def parse_resume_id(value: Any) -> UUID | None:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _uuid_str(value: Any) -> str:
    if isinstance(value, UUID):
        return str(value)
    return str(value) if value is not None else ""


def _as_iso(value: Any) -> str | None:
    if value is None:
        return None
    if value == "":
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _as_date_str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return text or None


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return []


def _field(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    return None


def empty_profile() -> dict[str, Any]:
    return {
        "full_name": "",
        "years_experience": None,
        "current_title": None,
        "target_title": None,
        "summary": "",
        "skills": [],
    }


def confirm_error(archive: dict[str, Any] | None) -> str | None:
    payload = archive if isinstance(archive, dict) else {}
    projects = payload.get("projects")
    if not isinstance(projects, list) or not projects:
        return CONFIRM_ERROR
    for item in projects:
        if not isinstance(item, dict):
            return CONFIRM_ERROR
        name = _field(item, "name")
        responsibilities = _field(item, "responsibilities")
        if not str(name or "").strip() or not str(responsibilities or "").strip():
            return CONFIRM_ERROR
    return None


def _public_job(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _uuid_str(row.get("id")),
        "company": row.get("company") or "",
        "jobTitle": _field(row, "job_title", "jobTitle") or "",
        "startedOn": _as_date_str(_field(row, "started_on", "startedOn")),
        "endedOn": _as_date_str(_field(row, "ended_on", "endedOn")),
        "isCurrent": bool(_field(row, "is_current", "isCurrent")),
        "summary": row.get("summary") or "",
        "sortOrder": int(row.get("sort_order") if row.get("sort_order") is not None else row.get("sortOrder") or 0),
    }


def _public_project(row: dict[str, Any]) -> dict[str, Any]:
    job_id = _field(row, "job_id", "jobId")
    return {
        "id": _uuid_str(row.get("id")),
        "jobId": _uuid_str(job_id) if job_id not in (None, "") else None,
        "name": row.get("name") or "",
        "company": row.get("company"),
        "jobRole": _field(row, "job_role", "jobRole"),
        "startedOn": _as_date_str(_field(row, "started_on", "startedOn")),
        "endedOn": _as_date_str(_field(row, "ended_on", "endedOn")),
        "techStack": _as_str_list(_field(row, "tech_stack", "techStack")),
        "responsibilities": row.get("responsibilities") or "",
        "outcomes": row.get("outcomes") or "",
        "sortOrder": int(row.get("sort_order") if row.get("sort_order") is not None else row.get("sortOrder") or 0),
    }


def public_resume(
    row: dict[str, Any] | None,
    profile: dict[str, Any] | None,
    jobs: list[dict[str, Any]] | None,
    projects: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    row = row or {}
    profile = profile or empty_profile()
    confirmed = _as_iso(profile.get("confirmed_at") if "confirmed_at" in profile else profile.get("confirmedAt"))
    return {
        "id": _uuid_str(row.get("id")),
        "fileName": row.get("file_name") or row.get("fileName") or "",
        "fileMime": row.get("file_mime") if "file_mime" in row else row.get("fileMime"),
        "fileSizeBytes": row.get("file_size_bytes") if "file_size_bytes" in row else row.get("fileSizeBytes"),
        "parseStatus": row.get("parse_status") or row.get("parseStatus") or "",
        "parseError": row.get("parse_error") if "parse_error" in row else row.get("parseError"),
        "isActive": bool(row.get("is_active") if "is_active" in row else row.get("isActive")),
        "confirmedAt": confirmed,
        "updatedAt": _as_iso(row.get("updated_at") if "updated_at" in row else row.get("updatedAt")),
        "profile": {
            "fullName": profile.get("full_name") or profile.get("fullName") or "",
            "yearsExperience": profile.get("years_experience")
            if "years_experience" in profile
            else profile.get("yearsExperience"),
            "currentTitle": profile.get("current_title") if "current_title" in profile else profile.get("currentTitle"),
            "targetTitle": profile.get("target_title") if "target_title" in profile else profile.get("targetTitle"),
            "summary": profile.get("summary") or "",
            "skills": _as_str_list(profile.get("skills")),
            "confirmedAt": confirmed,
        },
        "jobs": [_public_job(item) for item in (jobs or []) if isinstance(item, dict)],
        "projects": [_public_project(item) for item in (projects or []) if isinstance(item, dict)],
    }


def insert_blank_profile(conn, resume_id: Any) -> None:
    conn.execute(
        """
        INSERT INTO resume_profiles (resume_id, full_name, summary, skills)
        VALUES (%s, '', '', ARRAY[]::text[])
        """,
        (resume_id,),
    )


def create_resume_row(
    conn,
    user_id: Any,
    file_name: str,
    file_mime: str | None,
    file_size_bytes: int | None,
    storage_key: str | None,
) -> dict[str, Any]:
    row = conn.execute(
        """
        INSERT INTO resumes (
            user_id, file_name, file_mime, file_size_bytes, storage_key, parse_status, is_active, updated_at
        ) VALUES (%s, %s, %s, %s, %s, 'uploaded', FALSE, now())
        RETURNING *
        """,
        (user_id, file_name or "", file_mime, file_size_bytes, storage_key),
    ).fetchone()
    return dict(row)


def _old_job_id(row: dict[str, Any]) -> str:
    value = row.get("id")
    if value in (None, ""):
        return ""
    return _uuid_str(value)


def _old_project_job_id(row: dict[str, Any]) -> str:
    value = row.get("jobId") if row.get("jobId") not in (None, "") else row.get("job_id")
    if value in (None, ""):
        return ""
    return _uuid_str(value)


def replace_archive(conn, resume_id: Any, body: dict[str, Any] | None) -> None:
    payload = body if isinstance(body, dict) else {}
    raw_jobs = [item for item in (payload.get("jobs") or []) if isinstance(item, dict)]
    raw_projects = [item for item in (payload.get("projects") or []) if isinstance(item, dict)]
    old_job_ids = [_old_job_id(item) for item in raw_jobs[:12]]
    old_project_job_ids = [_old_project_job_id(item) for item in raw_projects[:16]]
    archive = normalize_structured({"profile": payload.get("profile"), "jobs": raw_jobs, "projects": raw_projects})
    profile = archive["profile"]
    jobs = archive["jobs"]
    projects = archive["projects"]

    conn.execute("DELETE FROM resume_projects WHERE resume_id = %s", (resume_id,))
    conn.execute("DELETE FROM resume_jobs WHERE resume_id = %s", (resume_id,))

    id_map: dict[str, Any] = {}
    new_ids: list[Any] = []
    for index, job in enumerate(jobs):
        inserted = conn.execute(
            """
            INSERT INTO resume_jobs (
                resume_id, company, job_title, started_on, ended_on, is_current, summary, sort_order, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
            RETURNING id
            """,
            (
                resume_id,
                job.get("company") or "",
                job.get("job_title") or "",
                job.get("started_on"),
                job.get("ended_on"),
                bool(job.get("is_current")),
                job.get("summary") or "",
                int(job.get("sort_order") if job.get("sort_order") is not None else index),
            ),
        ).fetchone()
        new_id = inserted["id"]
        new_ids.append(new_id)
        old = old_job_ids[index] if index < len(old_job_ids) else ""
        if old:
            id_map[old] = new_id

    for index, project in enumerate(projects):
        job_id = None
        old_job = old_project_job_ids[index] if index < len(old_project_job_ids) else ""
        if old_job and old_job in id_map:
            job_id = id_map[old_job]
        elif project.get("_job_index") is not None:
            job_index = project["_job_index"]
            if isinstance(job_index, int) and 0 <= job_index < len(new_ids):
                job_id = new_ids[job_index]
        tech_stack = project.get("tech_stack") if isinstance(project.get("tech_stack"), list) else []
        conn.execute(
            """
            INSERT INTO resume_projects (
                resume_id, job_id, name, company, job_role, started_on, ended_on,
                is_current, tech_stack, responsibilities, outcomes, sort_order, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            """,
            (
                resume_id,
                job_id,
                project.get("name") or "",
                project.get("company"),
                project.get("job_role"),
                project.get("started_on"),
                project.get("ended_on"),
                bool(project.get("is_current")),
                list(tech_stack),
                project.get("responsibilities") or "",
                project.get("outcomes") or "",
                int(project.get("sort_order") if project.get("sort_order") is not None else index),
            ),
        )

    conn.execute(
        """
        INSERT INTO resume_profiles (
            resume_id, full_name, years_experience, current_title, target_title, summary, skills, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (resume_id) DO UPDATE SET
            full_name = EXCLUDED.full_name,
            years_experience = EXCLUDED.years_experience,
            current_title = EXCLUDED.current_title,
            target_title = EXCLUDED.target_title,
            summary = EXCLUDED.summary,
            skills = EXCLUDED.skills,
            updated_at = now()
        """,
        (
            resume_id,
            profile.get("full_name") or "",
            profile.get("years_experience"),
            profile.get("current_title"),
            profile.get("target_title"),
            profile.get("summary") or "",
            list(profile.get("skills") or []),
        ),
    )


def load_archive(conn, resume_id: Any) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]] | None:
    resume_id = parse_resume_id(resume_id)
    if resume_id is None:
        return None
    row = conn.execute("SELECT * FROM resumes WHERE id = %s", (resume_id,)).fetchone()
    if not row:
        return None
    profile = conn.execute("SELECT * FROM resume_profiles WHERE resume_id = %s", (resume_id,)).fetchone()
    jobs = list(
        conn.execute(
            "SELECT * FROM resume_jobs WHERE resume_id = %s ORDER BY sort_order, created_at",
            (resume_id,),
        )
    )
    projects = list(
        conn.execute(
            "SELECT * FROM resume_projects WHERE resume_id = %s ORDER BY sort_order, created_at",
            (resume_id,),
        )
    )
    return dict(row), (dict(profile) if profile else None), [dict(item) for item in jobs], [dict(item) for item in projects]


def set_active(conn, user_id: Any, resume_id: Any) -> None:
    conn.execute(
        "UPDATE resumes SET is_active = FALSE, updated_at = now() WHERE user_id = %s",
        (user_id,),
    )
    conn.execute(
        """
        UPDATE resumes
        SET is_active = TRUE, updated_at = now()
        WHERE id = %s AND user_id = %s
        """,
        (resume_id, user_id),
    )


def get_resume_for_user(conn, user_id: Any, resume_id: Any):
    resume_id = parse_resume_id(resume_id)
    if resume_id is None:
        return None
    loaded = load_archive(conn, resume_id)
    if not loaded:
        return None
    row = loaded[0]
    if _uuid_str(row.get("user_id")) != _uuid_str(user_id):
        return None
    return loaded


def list_resumes(conn, user_id: Any) -> dict[str, Any]:
    rows = list(
        conn.execute(
            """
            SELECT r.id, r.file_name, r.parse_status, r.parse_error, r.is_active, r.updated_at, p.confirmed_at
            FROM resumes r
            LEFT JOIN resume_profiles p ON p.resume_id = r.id
            WHERE r.user_id = %s
            ORDER BY r.created_at DESC
            """,
            (user_id,),
        )
    )
    items: list[dict[str, Any]] = []
    active_id = None
    for row in rows:
        item_id = _uuid_str(row["id"])
        if row.get("is_active"):
            active_id = item_id
        items.append(
            {
                "id": item_id,
                "fileName": row.get("file_name") or "",
                "parseStatus": row.get("parse_status") or "",
                "parseError": row.get("parse_error"),
                "isActive": bool(row.get("is_active")),
                "confirmedAt": _as_iso(row.get("confirmed_at")),
                "updatedAt": _as_iso(row.get("updated_at")),
            }
        )
    return {"items": items, "activeId": active_id}


def replace_and_load(conn, user_id: Any, resume_id: Any, body: dict[str, Any] | None) -> dict[str, Any]:
    resume_id = parse_resume_id(resume_id)
    if resume_id is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    loaded = get_resume_for_user(conn, user_id, resume_id)
    if not loaded:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    replace_archive(conn, resume_id, body)
    loaded = load_archive(conn, resume_id)
    if not loaded:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    row, profile, jobs, projects = loaded
    return public_resume(row, profile, jobs, projects)


def confirm_resume(conn, user_id: Any, resume_id: Any) -> dict[str, Any]:
    resume_id = parse_resume_id(resume_id)
    if resume_id is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    loaded = get_resume_for_user(conn, user_id, resume_id)
    if not loaded:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    row, profile, jobs, projects = loaded
    error = confirm_error({"projects": projects})
    if error:
        raise HTTPException(status_code=400, detail=error)
    if profile is None:
        insert_blank_profile(conn, resume_id)
    updated = conn.execute(
        """
        UPDATE resume_profiles
        SET confirmed_at = now(), updated_at = now()
        WHERE resume_id = %s
        RETURNING confirmed_at
        """,
        (resume_id,),
    ).fetchone()
    if updated and updated.get("confirmed_at"):
        set_active(conn, user_id, resume_id)
    loaded = load_archive(conn, resume_id)
    if not loaded:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    row, profile, jobs, projects = loaded
    return public_resume(row, profile, jobs, projects)


PARSE_FAIL_MESSAGE = "解析失败，请手填档案或换一份文件。"


def should_skip_archive_replace(confirmed_at: Any, named_project_count: int) -> bool:
    return confirmed_at is not None or int(named_project_count or 0) > 0


def _named_project_count(conn, resume_id: Any) -> int:
    named = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM resume_projects
        WHERE resume_id = %s AND COALESCE(TRIM(name), '') <> ''
        """,
        (resume_id,),
    ).fetchone()
    return int(named.get("n") or 0) if named else 0


def run_parse_job(resume_id: Any) -> None:
    resume_id = parse_resume_id(resume_id)
    if resume_id is None:
        return
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM resumes WHERE id = %s", (resume_id,)).fetchone()
            if not row:
                return
            profile = conn.execute(
                "SELECT confirmed_at FROM resume_profiles WHERE resume_id = %s",
                (resume_id,),
            ).fetchone()
            confirmed_at = profile.get("confirmed_at") if profile else None
            named_count = _named_project_count(conn, resume_id)
            freeze_status = should_skip_archive_replace(confirmed_at, named_count)
            if not freeze_status:
                conn.execute(
                    """
                    UPDATE resumes
                    SET parse_status = 'parsing', parse_error = NULL, updated_at = now()
                    WHERE id = %s
                    """,
                    (resume_id,),
                )
            storage_key = row.get("storage_key")
            file_name = row.get("file_name") or ""
            mime = row.get("file_mime") or ""
        if not storage_key:
            raise FileNotFoundError("missing storage")
        data = read_stored(Path(storage_key))
        kind = file_kind(file_name, mime)
        result = run_parse_pipeline(kind, data, mime)
        with get_conn() as conn:
            profile = conn.execute(
                "SELECT confirmed_at FROM resume_profiles WHERE resume_id = %s",
                (resume_id,),
            ).fetchone()
            confirmed_at = profile.get("confirmed_at") if profile else None
            named_count = _named_project_count(conn, resume_id)
            if confirmed_at is not None:
                conn.execute(
                    """
                    UPDATE resumes
                    SET raw_text = %s, updated_at = now()
                    WHERE id = %s
                    """,
                    (result.get("raw_text") or "", resume_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE resumes
                    SET raw_text = %s, parse_status = %s, parse_error = %s, updated_at = now()
                    WHERE id = %s
                    """,
                    (
                        result.get("raw_text") or "",
                        result.get("status") or "failed",
                        result.get("error"),
                        resume_id,
                    ),
                )
            if not should_skip_archive_replace(confirmed_at, named_count):
                replace_archive(conn, resume_id, result.get("archive") or {})
    except Exception as err:
        log_event("resume_parse_failed", resumeId=str(resume_id), error=str(err))
        with get_conn() as conn:
            profile = conn.execute(
                "SELECT confirmed_at FROM resume_profiles WHERE resume_id = %s",
                (resume_id,),
            ).fetchone()
            confirmed_at = profile.get("confirmed_at") if profile else None
            named_count = _named_project_count(conn, resume_id)
            if should_skip_archive_replace(confirmed_at, named_count):
                return
            conn.execute(
                """
                UPDATE resumes
                SET parse_status = 'failed', parse_error = %s, updated_at = now()
                WHERE id = %s
                """,
                (PARSE_FAIL_MESSAGE, resume_id),
            )
