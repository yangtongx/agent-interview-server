from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from server.db import get_conn, init_schema

_cache: dict[str, Any] | None = None
_etag: str | None = None


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _as_obj(value: Any) -> dict | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _row_to_question(row: dict[str, Any], labels: dict[str, dict[str, list[str]]]) -> dict[str, Any]:
    source = _as_obj(row.get("source"))
    tagged = labels.get(row["id"]) or {}
    node = {
        "id": row["id"],
        "level": int(row["level"]),
        "topicId": row["topic_id"],
        "difficulty": row["difficulty"],
        "angle": row["angle"] or "",
        "title": row["title"],
        "question": row["question"],
        "gist": row["gist"] or "",
        "points": _as_list(row.get("points")),
        "bonus": row["bonus"] or "",
        "redFlags": row["red_flags"] or "",
        "fullAnswer": row["full_answer"] or "",
        "status": row.get("status") or "published",
        "contentVersion": row.get("content_version") or "",
        "experienceLevel": row.get("experience_level") or "",
        "questionType": row.get("question_type") or "",
        "audiences": tagged.get("audience") or [],
        "roles": tagged.get("role") or [],
        "domains": tagged.get("domain") or [],
        "capabilities": tagged.get("capability") or [],
        "children": [],
    }
    if row.get("parent_id"):
        node["parentId"] = row["parent_id"]
    if row.get("prerequisite_id"):
        node["prerequisiteId"] = row["prerequisite_id"]
    if source and source.get("label"):
        node["source"] = source
    return node


def _assemble(rows: list[dict[str, Any]], labels: dict[str, dict[str, list[str]]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    children: dict[str | None, list[str]] = defaultdict(list)
    for row in rows:
        node = _row_to_question(row, labels)
        by_id[node["id"]] = node
        children[row.get("parent_id")].append(node["id"])
    for qid, node in by_id.items():
        node["children"] = [by_id[cid] for cid in children.get(qid, []) if cid in by_id]
    return [by_id[qid] for qid in children.get(None, []) if qid in by_id]


def _count_nodes(nodes: list[dict[str, Any]]) -> int:
    total = 0
    stack = list(nodes)
    while stack:
        node = stack.pop()
        total += 1
        stack.extend(node.get("children") or [])
    return total


def _load_labels(conn) -> dict[str, dict[str, list[str]]]:
    grouped: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for row in conn.execute("SELECT question_id, kind, value FROM question_labels"):
        bucket = grouped[row["question_id"]][row["kind"]]
        if row["value"] not in bucket:
            bucket.append(row["value"])
    return {qid: dict(kinds) for qid, kinds in grouped.items()}


def load_paths(conn=None) -> list[dict[str, Any]]:
    def read(cur) -> list[dict[str, Any]]:
        paths = list(
            cur.execute(
                """
                SELECT id, name, summary, sort_order
                FROM learning_paths
                ORDER BY sort_order, id
                """
            )
        )
        items = list(
            cur.execute(
                """
                SELECT path_id, topic_id, sort_order
                FROM learning_path_items
                ORDER BY path_id, sort_order
                """
            )
        )
        by_path: dict[str, list[str]] = defaultdict(list)
        for row in items:
            by_path[row["path_id"]].append(row["topic_id"])
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "summary": row["summary"] or "",
                "topicIds": by_path.get(row["id"]) or [],
            }
            for row in paths
        ]

    if conn is not None:
        return read(conn)
    with get_conn() as owned:
        return read(owned)


def load_release(conn=None) -> dict[str, Any] | None:
    def read(cur) -> dict[str, Any] | None:
        row = cur.execute(
            """
            SELECT id, version, summary, published_at
            FROM content_releases
            WHERE is_current = TRUE
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        published = row["published_at"]
        return {
            "id": int(row["id"]),
            "version": row["version"],
            "summary": row["summary"] or "",
            "publishedAt": published.isoformat() if published else "",
        }

    if conn is not None:
        return read(conn)
    with get_conn() as owned:
        return read(owned)


def bank_etag(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f'"{digest}"'


def etag_matches(if_none_match: str | None, etag: str) -> bool:
    incoming = (if_none_match or "").strip()
    if not incoming or not etag:
        return False
    if incoming == "*":
        return True
    want = etag.strip()
    for part in incoming.split(","):
        token = part.strip()
        if token.startswith("W/"):
            token = token[2:].strip()
        if token == want:
            return True
    return False


def cached_etag() -> str:
    load_bank()
    return _etag or bank_etag(load_bank())


def load_bank(force: bool = False) -> dict[str, Any]:
    global _cache, _etag
    if _cache is not None and not force:
        return _cache
    init_schema()
    with get_conn() as conn:
        topics = list(
            conn.execute(
                """
                SELECT id, bank, sort_order, name, english, summary, interviewer_note
                FROM topics
                ORDER BY bank, sort_order, id
                """
            )
        )
        questions = list(
            conn.execute(
                """
                SELECT id, parent_id, prerequisite_id, level, topic_id, difficulty,
                       angle, title, question, gist, points, bonus, red_flags,
                       source, full_answer, sort_order, status, content_version,
                       experience_level, question_type
                FROM questions
                WHERE status = 'published'
                ORDER BY topic_id, level, sort_order, id
                """
            )
        )
        intro_row = conn.execute(
            "SELECT value FROM app_meta WHERE key = %s", ("ailian_intro",)
        ).fetchone()
        candidate_row = conn.execute(
            "SELECT value FROM app_meta WHERE key = %s", ("candidate",)
        ).fetchone()
        labels = _load_labels(conn)
        paths = load_paths(conn)
        release = load_release(conn)

    if not topics or not questions:
        raise RuntimeError("数据库里还没有题库。请先运行 python -m server.seed")

    topic_payload = [
        {
            "id": row["id"],
            "index": int(row["sort_order"] or 0),
            "bank": row["bank"],
            "name": row["name"],
            "english": row["english"] or "",
            "summary": row["summary"] or "",
            "interviewerNote": row["interviewer_note"] or "",
        }
        for row in topics
    ]
    tree = _assemble(questions, labels)
    alibaba_topics = [t for t in topic_payload if t["bank"] == "alibaba"]
    ailian_topics = [t for t in topic_payload if t["bank"] == "ailian"]
    alibaba_parents = [
        q for q in tree if any(t["id"] == q["topicId"] and t["bank"] == "alibaba" for t in topic_payload)
    ]
    ailian_parents = [
        q for q in tree if any(t["id"] == q["topicId"] and t["bank"] == "ailian" for t in topic_payload)
    ]
    intro = _as_obj(intro_row["value"]) if intro_row else {"candidate": "", "title": "", "body": ""}
    candidate = _as_obj(candidate_row["value"]) if candidate_row else None
    payload = {
        "topics": topic_payload,
        "questions": tree,
        "ailianIntro": intro or {"candidate": "", "title": "", "body": ""},
        "candidate": candidate,
        "paths": paths,
        "release": release,
        "stats": {
            "totalParents": len(tree),
            "totalQuestions": _count_nodes(tree),
            "alibabaParents": len(alibaba_parents),
            "ailianParents": len(ailian_parents),
            "alibabaTopics": len(alibaba_topics),
            "ailianTopics": len(ailian_topics),
            "fromFiles": True,
        },
    }
    _cache = payload
    _etag = bank_etag(payload)
    return payload


def flatten_parents(tree: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    nodes = tree if tree is not None else load_bank()["questions"]
    return [node for node in nodes if int(node.get("level") or 1) == 1]


def clear_cache() -> None:
    global _cache, _etag
    _cache = None
    _etag = None
