from __future__ import annotations

from typing import Any


def preferred_parent_ids(audience: str, parents: list[dict[str, Any]]) -> set[str]:
    if audience != "transition":
        return set()
    return {row["id"] for row in parents if "Java 后端" in (row.get("roles") or [])}


def pick_next_parent(
    path_topic_ids: list[str],
    parents: list[dict[str, Any]],
    mastered: set[str],
    preferred_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    by_topic: dict[str, list[dict[str, Any]]] = {}
    for parent in parents:
        by_topic.setdefault(parent["topicId"], []).append(parent)
    preferred = preferred_ids or set()
    for topic_id in path_topic_ids:
        items = list(by_topic.get(topic_id) or [])
        if preferred:
            items.sort(key=lambda row: (0 if row["id"] in preferred else 1))
        for row in items:
            if row["id"] not in mastered:
                return row
    return None


def build_home(
    audience: str,
    paths: list[dict[str, Any]],
    parents: list[dict[str, Any]],
    topics: list[dict[str, Any]],
    mastered: set[str],
    preferred_ids: set[str],
    review_ids: list[str],
    weak_ids: list[str],
) -> dict[str, Any]:
    topic_name = {t["id"]: t["name"] for t in topics}
    track_id = audience if audience in ("transition", "experienced") else "transition"
    path = next((p for p in paths if p["id"] == track_id), paths[0] if paths else None)
    next_item = None
    if path:
        picked = pick_next_parent(path.get("topicIds") or [], parents, mastered, preferred_ids)
        if picked:
            next_item = {
                "questionId": picked["id"],
                "title": picked.get("title") or "",
                "question": picked.get("question") or "",
                "topicId": picked["topicId"],
                "topicName": topic_name.get(picked["topicId"], ""),
                "reason": "当前路线的下一道核心题",
            }
    by_id = {p["id"]: p for p in parents}

    def pack(qid: str, reason: str) -> dict[str, Any] | None:
        row = by_id.get(qid)
        if not row:
            return None
        return {
            "questionId": row["id"],
            "title": row.get("title") or "",
            "question": row.get("question") or "",
            "topicId": row["topicId"],
            "topicName": topic_name.get(row["topicId"], ""),
            "reason": reason,
        }

    review = [pack(qid, "今天该复习") for qid in review_ids]
    weak = [pack(qid, "近期答得不稳") for qid in weak_ids]
    on_path = 0
    done_on_path = 0
    if path:
        path_topics = set(path.get("topicIds") or [])
        on_path_parents = [p for p in parents if p["topicId"] in path_topics]
        on_path = len(on_path_parents)
        done_on_path = sum(1 for p in on_path_parents if p["id"] in mastered)
    return {
        "audience": audience if audience in ("unset", "transition", "experienced") else "unset",
        "path": path,
        "paths": paths,
        "next": next_item,
        "review": [item for item in review if item],
        "weak": [item for item in weak if item],
        "stats": {
            "mastered": len(mastered),
            "onPath": on_path,
            "doneOnPath": done_on_path,
        },
    }
