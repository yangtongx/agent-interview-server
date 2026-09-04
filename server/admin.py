from __future__ import annotations

import secrets
from typing import Any

from fastapi import HTTPException, Request

from server.bank import clear_cache
from server.config import ADMIN_TOKEN
from server.db import get_conn


def require_admin(request: Request) -> None:
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="服务器未配置 ADMIN_TOKEN。")
    token = (request.headers.get("x-admin-token") or request.query_params.get("token") or "").strip()
    if not token or len(token) != len(ADMIN_TOKEN) or not secrets.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="中台令牌不对。")


def admin_stats() -> dict[str, Any]:
    with get_conn() as conn:
        questions = conn.execute("SELECT COUNT(*) AS n FROM questions").fetchone()["n"]
        published = conn.execute(
            "SELECT COUNT(*) AS n FROM questions WHERE status = 'published'"
        ).fetchone()["n"]
        users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        records = conn.execute("SELECT COUNT(*) AS n FROM practice_records").fetchone()["n"]
        release = conn.execute(
            "SELECT version, summary, published_at FROM content_releases WHERE is_current = TRUE"
        ).fetchone()
    return {
        "questions": int(questions),
        "published": int(published),
        "users": int(users),
        "practiceRecords": int(records),
        "release": {
            "version": release["version"],
            "summary": release["summary"],
            "publishedAt": release["published_at"].isoformat(),
        }
        if release
        else None,
    }


def list_questions(q: str, status: str, topic_id: str, page: int, size: int) -> dict[str, Any]:
    size = min(max(size, 1), 100)
    page = max(page, 1)
    offset = (page - 1) * size
    clauses = ["1=1"]
    args: list[Any] = []
    if q:
        clauses.append("(title ILIKE %s OR question ILIKE %s OR id ILIKE %s)")
        like = f"%{q}%"
        args.extend([like, like, like])
    if status in ("draft", "published", "retired"):
        clauses.append("status = %s")
        args.append(status)
    if topic_id:
        clauses.append("topic_id = %s")
        args.append(topic_id)
    where = " AND ".join(clauses)
    with get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS n FROM questions WHERE {where}", args).fetchone()["n"]
        rows = list(
            conn.execute(
                f"""
                SELECT id, topic_id, level, status, difficulty, title, question, experience_level, question_type, updated_at
                FROM questions
                WHERE {where}
                ORDER BY updated_at DESC, id
                LIMIT %s OFFSET %s
                """,
                [*args, size, offset],
            )
        )
    return {
        "total": int(total),
        "page": page,
        "size": size,
        "items": [
            {
                "id": row["id"],
                "topicId": row["topic_id"],
                "level": int(row["level"]),
                "status": row["status"],
                "difficulty": row["difficulty"],
                "title": row["title"],
                "question": row["question"],
                "experienceLevel": row["experience_level"] or "",
                "questionType": row["question_type"] or "",
                "updatedAt": row["updated_at"].isoformat() if row["updated_at"] else "",
            }
            for row in rows
        ],
    }


def get_question(qid: str) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM questions WHERE id = %s", (qid,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="题目不存在。")
        labels = list(
            conn.execute(
                "SELECT kind, value FROM question_labels WHERE question_id = %s ORDER BY kind, value",
                (qid,),
            )
        )
    grouped: dict[str, list[str]] = {}
    for item in labels:
        grouped.setdefault(item["kind"], []).append(item["value"])
    return {
        "id": row["id"],
        "parentId": row["parent_id"],
        "topicId": row["topic_id"],
        "level": int(row["level"]),
        "status": row["status"],
        "difficulty": row["difficulty"],
        "angle": row["angle"],
        "title": row["title"],
        "question": row["question"],
        "gist": row["gist"],
        "points": row["points"],
        "bonus": row["bonus"],
        "redFlags": row["red_flags"],
        "fullAnswer": row["full_answer"],
        "experienceLevel": row["experience_level"] or "",
        "questionType": row["question_type"] or "",
        "contentVersion": row["content_version"],
        "labels": grouped,
    }


def patch_question(qid: str, body: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "status": "status",
        "title": "title",
        "question": "question",
        "gist": "gist",
        "bonus": "bonus",
        "redFlags": "red_flags",
        "fullAnswer": "full_answer",
        "angle": "angle",
        "difficulty": "difficulty",
        "experienceLevel": "experience_level",
        "questionType": "question_type",
    }
    sets = []
    args: list[Any] = []
    for key, column in allowed.items():
        if key not in body:
            continue
        value = body[key]
        if key == "status" and value not in ("draft", "published", "retired"):
            raise HTTPException(status_code=400, detail="status 只能是 draft / published / retired。")
        if key == "experienceLevel" and value and value not in ("入门", "进阶", "生产实战", "架构"):
            raise HTTPException(status_code=400, detail="经验层级不在允许范围内。")
        if key == "questionType" and value and value not in ("概念", "项目深挖", "故障复盘", "系统设计", "编码"):
            raise HTTPException(status_code=400, detail="题型不在允许范围内。")
        sets.append(f"{column} = %s")
        args.append(value if value != "" else None)
    labels = body.get("labels") if isinstance(body.get("labels"), dict) else None
    with get_conn() as conn:
        exists = conn.execute("SELECT id FROM questions WHERE id = %s", (qid,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="题目不存在。")
        if sets:
            args.append(qid)
            conn.execute(
                f"UPDATE questions SET {', '.join(sets)}, updated_at = now() WHERE id = %s",
                args,
            )
        if labels is not None:
            conn.execute("DELETE FROM question_labels WHERE question_id = %s", (qid,))
            for kind, values in labels.items():
                if kind not in ("audience", "role", "domain", "capability"):
                    continue
                if not isinstance(values, list):
                    continue
                for value in values:
                    if not isinstance(value, str) or not value.strip():
                        continue
                    conn.execute(
                        """
                        INSERT INTO question_labels (question_id, kind, value)
                        VALUES (%s, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (qid, kind, value.strip()),
                    )
    clear_cache()
    return get_question(qid)


def list_releases() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = list(
            conn.execute(
                "SELECT id, version, summary, published_at, is_current FROM content_releases ORDER BY id DESC"
            )
        )
    return [
        {
            "id": int(row["id"]),
            "version": row["version"],
            "summary": row["summary"],
            "publishedAt": row["published_at"].isoformat() if row["published_at"] else "",
            "isCurrent": bool(row["is_current"]),
        }
        for row in rows
    ]


def publish_release(version: str, summary: str) -> dict[str, Any]:
    version = version.strip()
    if not version:
        raise HTTPException(status_code=400, detail="需要版本号。")
    with get_conn() as conn:
        conn.execute("UPDATE content_releases SET is_current = FALSE WHERE is_current = TRUE")
        row = conn.execute(
            """
            INSERT INTO content_releases (version, summary, is_current)
            VALUES (%s, %s, TRUE)
            ON CONFLICT (version) DO UPDATE SET
                summary = EXCLUDED.summary,
                is_current = TRUE,
                published_at = now()
            RETURNING id, version, summary, published_at, is_current
            """,
            (version, summary.strip()),
        ).fetchone()
    clear_cache()
    return {
        "id": int(row["id"]),
        "version": row["version"],
        "summary": row["summary"],
        "publishedAt": row["published_at"].isoformat(),
        "isCurrent": True,
    }


def list_topics() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = list(
            conn.execute(
                "SELECT id, bank, sort_order, name FROM topics ORDER BY bank, sort_order, id"
            )
        )
    return [
        {"id": row["id"], "bank": row["bank"], "index": int(row["sort_order"] or 0), "name": row["name"]}
        for row in rows
    ]
