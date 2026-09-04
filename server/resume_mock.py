from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from server.bank import flatten_parents
from server.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from server.db import get_conn
from server.http_client import http
from server.judge import completions_url, extract_json_object, looks_like_html
from server.resumes import NOT_FOUND, get_resume_for_user, parse_resume_id

INVENTED_ENTITIES = (
    "淘宝",
    "阿里",
    "字节",
    "腾讯",
    "美团",
    "京东",
    "华为",
    "双十一",
    "拼多多",
    "百度",
    "网易",
    "快手",
    "小红书",
    "蚂蚁",
    "抖音",
    "微信",
    "钉钉",
    "饿了么",
)
ALLOWED_ANGLES = frozenset({"ownership", "metrics", "incident", "tradeoff", "transition"})


def _tech_stack(project: dict[str, Any]) -> list[str]:
    tech = project.get("tech_stack")
    if tech is None:
        tech = project.get("techStack")
    if not isinstance(tech, list):
        return []
    return [str(item).strip() for item in tech if str(item).strip()]


def _field(project: dict[str, Any], snake: str, camel: str) -> str:
    value = project.get(snake)
    if value is None:
        value = project.get(camel)
    return str(value or "")


def project_slice(project: dict[str, Any] | None) -> dict[str, Any]:
    row = project if isinstance(project, dict) else {}
    return {
        "name": str(row.get("name") or ""),
        "company": str(row.get("company") or ""),
        "job_role": _field(row, "job_role", "jobRole"),
        "tech_stack": _tech_stack(row),
        "responsibilities": str(row.get("responsibilities") or ""),
        "outcomes": str(row.get("outcomes") or ""),
    }


def _project_corpus(project: dict[str, Any] | None) -> str:
    sliced = project_slice(project)
    return "\n".join(
        [
            sliced["name"],
            sliced["company"],
            sliced["job_role"],
            sliced["responsibilities"],
            sliced["outcomes"],
            " ".join(sliced["tech_stack"]),
        ]
    )


def mentions_unknown_entity(question: str, project: dict[str, Any] | None) -> bool:
    text = question or ""
    corpus = _project_corpus(project)
    for name in INVENTED_ENTITIES:
        if name in text and name not in corpus:
            return True
    return False


def fallback_question(project: dict[str, Any] | None) -> str:
    row = project if isinstance(project, dict) else {}
    name = (row.get("name") or "这个项目").strip()
    duty = (row.get("responsibilities") or "").strip()[:120]
    return f"在「{name}」里你写过：{duty}。你本人负责哪一段，怎么验收？"


def _fallback_payload(project: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "question": fallback_question(project),
        "gist": "讲清本人职责和验收",
        "points": [{"heading": "职责", "body": "讲清自己做的部分"}],
        "angle": "ownership",
    }


def build_interviewer_messages(
    project: dict[str, Any],
    audience: str,
    asked: list[str] | None = None,
    last_answer: str = "",
    raw_text: str | None = None,
) -> list[dict[str, str]]:
    del raw_text
    sliced = project_slice(project)
    focus = (
        "候选人是转岗方向：优先追问 Java 工作如何映射到 Agent。"
        if audience != "experienced"
        else "候选人是有经验方向：优先追问指标、事故、方案取舍。"
    )
    system = (
        "你是 AI Agent 面试官。只根据用户给出的项目切片出一道中文题。"
        "只问切片里出现过的事实，不要发明公司、指标、事故、项目名。"
        "必须让候选人讲清本人负责哪一段、怎么验收。"
        "不要使用简历原文；你看不到原文。"
        f"{focus}"
        "只返回一个 JSON 对象，不要 markdown："
        '{"question":"...","gist":"...","points":[{"heading":"","body":""}],'
        '"angle":"ownership|metrics|incident|tradeoff|transition"}'
    )
    user = json.dumps(
        {
            "project": sliced,
            "asked": [str(item) for item in (asked or []) if str(item).strip()],
            "last_answer": last_answer or "",
        },
        ensure_ascii=False,
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _as_points(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return [{"heading": "职责", "body": "讲清自己做的部分"}]
    out: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        heading = item.get("heading") if isinstance(item.get("heading"), str) else ""
        body = item.get("body") if isinstance(item.get("body"), str) else ""
        if heading.strip() or body.strip():
            out.append({"heading": heading.strip()[:80], "body": body.strip()[:800]})
        if len(out) >= 6:
            break
    return out or [{"heading": "职责", "body": "讲清自己做的部分"}]


def _normalize_generated(raw: Any) -> dict[str, Any] | None:
    obj = raw if isinstance(raw, dict) else None
    if obj is None:
        return None
    question = obj.get("question")
    if not isinstance(question, str) or not question.strip():
        return None
    gist = obj.get("gist")
    if not isinstance(gist, str) or not gist.strip():
        gist = "讲清本人职责和验收"
    angle = obj.get("angle") if obj.get("angle") in ALLOWED_ANGLES else "ownership"
    return {
        "question": question.strip(),
        "gist": gist.strip(),
        "points": _as_points(obj.get("points")),
        "angle": angle,
    }


def _complete_question(messages: list[dict[str, str]]) -> dict[str, Any] | None:
    try:
        response = http().post(
            completions_url(LLM_BASE_URL),
            headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
            json={"model": LLM_MODEL, "temperature": 0.3, "messages": messages},
            timeout=60.0,
        )
        if response.status_code >= 400 or looks_like_html(response.text):
            return None
        payload = response.json()
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not (isinstance(choices, list) and choices):
            return None
        message = choices[0].get("message") if isinstance(choices[0], dict) else {}
        content = message.get("content") if isinstance(message, dict) else ""
        if not isinstance(content, str) or not content.strip():
            return None
        return _normalize_generated(extract_json_object(content))
    except Exception:
        return None


def generate_resume_question(
    project: dict[str, Any],
    audience: str,
    asked: list[str] | None,
    last_answer: str = "",
) -> dict[str, Any]:
    fallback = _fallback_payload(project)
    if not LLM_API_KEY:
        return fallback
    messages = build_interviewer_messages(project, audience, asked, last_answer)
    parsed = _complete_question(messages)
    if parsed is None:
        return fallback
    if not mentions_unknown_entity(parsed["question"], project):
        return parsed
    parsed = _complete_question(messages)
    if parsed is None or mentions_unknown_entity(parsed["question"], project):
        return fallback
    return parsed


def pick_second_project(opener_id: str, projects: list[dict]) -> str | None:
    ordered = sorted(projects, key=lambda row: (int(row.get("sort_order") or 0), str(row.get("id"))))
    ids = [str(row["id"]) for row in ordered]
    if opener_id in ids:
        idx = ids.index(opener_id)
        after = ids[idx + 1 :]
        if after:
            return after[0]
        before = [i for i in ids if i != opener_id]
        return before[0] if before else None
    return ids[1] if len(ids) > 1 else None


def project_sequence(opener_id: str, projects: list[dict]) -> list[str]:
    second = pick_second_project(opener_id, projects)
    out = [opener_id]
    if second and second != opener_id:
        out.append(second)
    return out


def next_action(
    resume_items: list[dict],
    project_ids: list[str],
    bank_question_ids: list[str],
    bank_target: int = 3,
) -> tuple[str, str | None, int]:
    counts: dict[str, int] = {pid: 0 for pid in project_ids}
    for row in resume_items:
        pid = str(row.get("project_id") or "")
        if pid in counts:
            counts[pid] += 1
    for pid in project_ids:
        n = counts.get(pid, 0)
        if n < 3:
            return ("resume", pid, n + 1)
    if len(bank_question_ids) < bank_target:
        return ("bank", None, len(bank_question_ids) + 1)
    return ("done", None, 0)


def plan_bank_parent_ids(session_id: str, parents: list[dict], n: int) -> list[dict]:
    import random

    rng = random.Random(str(session_id))
    by_topic: dict[str, list[dict]] = {}
    for row in parents:
        by_topic.setdefault(str(row.get("topicId") or ""), []).append(row)
    topics = [t for t in by_topic if t]
    rng.shuffle(topics)
    picked: list[dict] = []
    for topic in topics:
        pool = list(by_topic[topic])
        if not pool:
            continue
        picked.append(pool[rng.randrange(0, len(pool))])
        if len(picked) >= n:
            break
    return picked


SESSION_NOT_FOUND = "场次不存在。"
NEED_ANSWER = "上一题先答完再继续。"
NEED_CONFIRMED = "请先确认档案并保留至少一个项目。"
KIND_RESUME_ONLY = "只支持按简历开场。"
PROJECT_NOT_ON_RESUME = "项目不属于这份档案。"
SESSION_FINISHED = "这场已经结束。"


def _id(value: Any) -> str:
    if isinstance(value, UUID):
        return str(value)
    return str(value) if value is not None else ""


def _parse_uuid(value: Any) -> UUID | None:
    return parse_resume_id(value)


def _progress(
    phase: str,
    project_index: int = 0,
    project_count: int = 0,
    question_in_project: int = 0,
) -> dict[str, Any]:
    return {
        "phase": phase,
        "projectIndex": project_index,
        "projectCount": project_count,
        "questionInProject": question_in_project,
        "maxQuestionsInProject": 3,
    }


def _done_payload(conn, session_id: Any, project_count: int) -> dict[str, Any]:
    conn.execute(
        """
        UPDATE mock_sessions
        SET status = 'finished', finished_at = now()
        WHERE id = %s
        """,
        (session_id,),
    )
    return {"itemKind": "done", "progress": _progress("done", project_count, project_count, 0)}


def create_session(user: dict[str, Any], body: dict[str, Any] | None) -> dict[str, str]:
    payload = body if isinstance(body, dict) else {}
    if payload.get("kind") != "resume":
        raise HTTPException(status_code=400, detail=KIND_RESUME_ONLY)
    resume_id = _parse_uuid(payload.get("resumeId"))
    if resume_id is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    with get_conn() as conn:
        loaded = get_resume_for_user(conn, user["id"], resume_id)
        if not loaded:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        row, profile, _jobs, projects = loaded
        confirmed = profile.get("confirmed_at") if profile else None
        if not row.get("is_active") or not confirmed or not projects:
            raise HTTPException(status_code=400, detail=NEED_CONFIRMED)
        opener = None
        raw_project = payload.get("projectId")
        if isinstance(raw_project, str) and raw_project.strip():
            opener = _parse_uuid(raw_project)
            if opener is None or not any(_parse_uuid(item.get("id")) == opener for item in projects):
                raise HTTPException(status_code=400, detail=PROJECT_NOT_ON_RESUME)
        else:
            ordered = sorted(
                projects,
                key=lambda item: (int(item.get("sort_order") or 0), _id(item.get("id"))),
            )
            opener = _parse_uuid(ordered[0]["id"])
        inserted = conn.execute(
            """
            INSERT INTO mock_sessions (user_id, mode, bank, status, resume_id, resume_project_id)
            VALUES (%s, 'mixed', 'all', 'active', %s, %s)
            RETURNING id
            """,
            (user["id"], resume_id, opener),
        ).fetchone()
        return {"id": _id(inserted["id"])}


def _require_active_session(conn, user_id: Any, sid: UUID) -> dict[str, Any]:
    session = conn.execute(
        """
        SELECT id, user_id, status, resume_id, resume_project_id
        FROM mock_sessions
        WHERE id = %s AND user_id = %s
        """,
        (sid, user_id),
    ).fetchone()
    if not session:
        raise HTTPException(status_code=404, detail=SESSION_NOT_FOUND)
    if session.get("status") != "active":
        raise HTTPException(status_code=400, detail=SESSION_FINISHED)
    return dict(session)


def _session_items(conn, sid: UUID) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT
                i.id,
                i.question_id,
                i.resume_question_id,
                i.sort_order,
                rq.project_id,
                rq.question AS asked_question
            FROM mock_session_items i
            LEFT JOIN resume_questions rq ON rq.id = i.resume_question_id
            WHERE i.session_id = %s
            ORDER BY i.sort_order
            """,
            (sid,),
        )
    ]


def _resume_projects(conn, resume_id: Any) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM resume_projects
            WHERE resume_id = %s
            ORDER BY sort_order, created_at
            """,
            (resume_id,),
        )
    ]


def _plan_turn(
    session: dict[str, Any],
    items: list[dict[str, Any]],
    projects: list[dict[str, Any]],
    sid: UUID,
    answer_text: str,
) -> dict[str, Any]:
    if items and not answer_text.strip():
        raise HTTPException(status_code=400, detail=NEED_ANSWER)
    opener = _id(session.get("resume_project_id")) or (_id(projects[0]["id"]) if projects else "")
    seq = project_sequence(opener, projects) if opener else []
    resume_items = [
        {"project_id": _id(item.get("project_id"))}
        for item in items
        if item.get("resume_question_id")
    ]
    bank_ids = [_id(item.get("question_id")) for item in items if item.get("question_id")]
    asked = [
        str(item.get("asked_question") or "")
        for item in items
        if item.get("resume_question_id") and str(item.get("asked_question") or "").strip()
    ]
    planned = plan_bank_parent_ids(_id(sid), flatten_parents(), 3)
    bank_target = min(3, len(planned))
    kind, project_id, level = next_action(resume_items, seq, bank_ids, bank_target=bank_target)
    remaining = [row for row in planned if _id(row.get("id")) not in bank_ids]
    if kind == "bank" and not remaining:
        kind = "done"
    return {
        "kind": kind,
        "project_id": str(project_id) if project_id else "",
        "level": int(level or 0),
        "next_order": len(items) + 1,
        "project_count": len(seq),
        "seq": seq,
        "asked": asked,
        "remaining": remaining,
        "resume_id": session["resume_id"],
    }


def _insert_bank_turn(conn, sid: UUID, plan: dict[str, Any]) -> dict[str, Any]:
    node = plan["remaining"][0]
    question_id = _id(node.get("id"))
    conn.execute(
        """
        INSERT INTO mock_session_items (session_id, question_id, sort_order)
        VALUES (%s, %s, %s)
        """,
        (sid, question_id, plan["next_order"]),
    )
    points = node.get("points") if isinstance(node.get("points"), list) else []
    project_count = int(plan["project_count"])
    return {
        "itemKind": "bank",
        "questionId": question_id,
        "question": {
            "question": node.get("question") or "",
            "gist": node.get("gist") or "",
            "points": points,
        },
        "progress": _progress("bank", project_count, project_count, 0),
    }


def _insert_resume_turn(
    conn,
    sid: UUID,
    plan: dict[str, Any],
    current: dict[str, Any],
    parent_id: Any,
    generated: dict[str, Any],
) -> dict[str, Any]:
    inserted_q = conn.execute(
        """
        INSERT INTO resume_questions (
            resume_id, project_id, parent_id, level, angle, question, gist, sort_order, status
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'ready')
        RETURNING id
        """,
        (
            plan["resume_id"],
            current["id"],
            parent_id,
            int(plan["level"]),
            str(generated.get("angle") or "ownership"),
            str(generated.get("question") or ""),
            str(generated.get("gist") or ""),
            plan["next_order"],
        ),
    ).fetchone()
    resume_question_id = inserted_q["id"]
    conn.execute(
        """
        INSERT INTO mock_session_items (session_id, resume_question_id, sort_order)
        VALUES (%s, %s, %s)
        """,
        (sid, resume_question_id, plan["next_order"]),
    )
    points = generated.get("points") if isinstance(generated.get("points"), list) else []
    project_id = str(plan["project_id"])
    seq = plan["seq"]
    project_index = seq.index(project_id) + 1 if project_id in seq else 1
    return {
        "itemKind": "resume",
        "resumeQuestionId": _id(resume_question_id),
        "question": {
            "question": generated.get("question") or "",
            "gist": generated.get("gist") or "",
            "points": points,
            "angle": generated.get("angle") or "ownership",
        },
        "progress": _progress("project", project_index, int(plan["project_count"]), int(plan["level"])),
    }


def take_turn(user: dict[str, Any], session_id: str, body: dict[str, Any] | None) -> dict[str, Any]:
    payload = body if isinstance(body, dict) else {}
    sid = _parse_uuid(session_id)
    if sid is None:
        raise HTTPException(status_code=404, detail=SESSION_NOT_FOUND)
    answer_text = payload.get("answerText") if isinstance(payload.get("answerText"), str) else ""
    with get_conn() as conn:
        session = _require_active_session(conn, user["id"], sid)
        items = _session_items(conn, sid)
        projects = _resume_projects(conn, session["resume_id"])
        plan = _plan_turn(session, items, projects, sid, answer_text)
        if plan["kind"] == "done":
            return _done_payload(conn, sid, plan["project_count"])
        if plan["kind"] == "bank":
            return _insert_bank_turn(conn, sid, plan)
        current = next((row for row in projects if _id(row.get("id")) == plan["project_id"]), None)
        if current is None:
            raise HTTPException(status_code=400, detail=PROJECT_NOT_ON_RESUME)
        parent_id = None
        if int(plan["level"]) > 1:
            for item in items:
                if item.get("resume_question_id") and _id(item.get("project_id")) == plan["project_id"]:
                    parent_id = item["resume_question_id"]
                    break
        sliced = project_slice(current)
        current_id = current["id"]
    generated = generate_resume_question(
        sliced,
        str(user.get("audience") or "unset"),
        plan["asked"],
        answer_text,
    )
    with get_conn() as conn:
        _require_active_session(conn, user["id"], sid)
        return _insert_resume_turn(conn, sid, plan, {"id": current_id}, parent_id, generated)


def finish_session(user: dict[str, Any], session_id: str) -> dict[str, str]:
    sid = _parse_uuid(session_id)
    if sid is None:
        raise HTTPException(status_code=404, detail=SESSION_NOT_FOUND)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM mock_sessions WHERE id = %s AND user_id = %s",
            (sid, user["id"]),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=SESSION_NOT_FOUND)
        conn.execute(
            """
            UPDATE mock_sessions
            SET status = 'finished', finished_at = COALESCE(finished_at, now())
            WHERE id = %s
            """,
            (sid,),
        )
        return {"status": "finished"}

