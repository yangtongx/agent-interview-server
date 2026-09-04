from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from server.db import dump_json, get_conn


def load_progress(user_id: str) -> dict[str, Any]:
    with get_conn() as conn:
        rows = list(
            conn.execute(
                """
                SELECT question_id, mastered, last_score, attempt_count, last_practiced_at, next_review_at, updated_at
                FROM user_progress
                WHERE user_id = %s
                """,
                (user_id,),
            )
        )
    mastered = [row["question_id"] for row in rows if row.get("mastered")]
    return {
        "mastered": mastered,
        "items": [
            {
                "questionId": row["question_id"],
                "mastered": bool(row["mastered"]),
                "lastScore": float(row["last_score"]) if row.get("last_score") is not None else None,
                "attemptCount": int(row["attempt_count"] or 0),
                "lastPracticedAt": row["last_practiced_at"].isoformat() if row.get("last_practiced_at") else None,
                "nextReviewAt": row["next_review_at"].isoformat() if row.get("next_review_at") else None,
                "updatedAt": row["updated_at"].isoformat() if row.get("updated_at") else None,
            }
            for row in rows
        ],
    }


def merge_progress(user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    ids = body.get("mastered")
    items = body.get("items") if isinstance(body.get("items"), list) else []
    with get_conn() as conn:
        valid = {
            row["id"]
            for row in conn.execute("SELECT id FROM questions")
        }
        if isinstance(ids, list):
            for qid in ids:
                if not isinstance(qid, str) or not qid or qid not in valid:
                    continue
                conn.execute(
                    """
                    INSERT INTO user_progress (user_id, question_id, mastered, updated_at)
                    VALUES (%s, %s, TRUE, now())
                    ON CONFLICT (user_id, question_id) DO UPDATE SET
                        mastered = TRUE,
                        updated_at = now()
                    """,
                    (user_id, qid),
                )
            current = {
                row["question_id"]
                for row in conn.execute(
                    "SELECT question_id FROM user_progress WHERE user_id = %s AND mastered = TRUE",
                    (user_id,),
                )
            }
            incoming = {qid for qid in ids if isinstance(qid, str) and qid in valid}
            for qid in current - incoming:
                conn.execute(
                    """
                    UPDATE user_progress
                    SET mastered = FALSE, updated_at = now()
                    WHERE user_id = %s AND question_id = %s
                    """,
                    (user_id, qid),
                )
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("questionId"), str):
                continue
            if item["questionId"] not in valid:
                continue
            conn.execute(
                """
                INSERT INTO user_progress (
                    user_id, question_id, mastered, last_score, attempt_count, last_practiced_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, now(), now())
                ON CONFLICT (user_id, question_id) DO UPDATE SET
                    mastered = EXCLUDED.mastered,
                    last_score = COALESCE(EXCLUDED.last_score, user_progress.last_score),
                    attempt_count = GREATEST(user_progress.attempt_count, EXCLUDED.attempt_count),
                    last_practiced_at = now(),
                    updated_at = now()
                """,
                (
                    user_id,
                    item["questionId"],
                    bool(item.get("mastered")),
                    item.get("lastScore"),
                    int(item.get("attemptCount") or 1),
                ),
            )
    return load_progress(user_id)


def add_practice(
    user_id: str,
    device_id: str | None,
    question_id: str,
    answer_text: str,
    input_mode: str,
    result: dict[str, Any],
) -> None:
    mode = result.get("mode")
    judge_mode = "llm" if mode == "llm" else "offline"
    verdict = result.get("verdict")
    if verdict not in ("过", "追问", "挂"):
        verdict = None
    score = result.get("score")
    try:
        score_val = float(score) if score is not None else None
    except (TypeError, ValueError):
        score_val = None
    if input_mode not in ("text", "voice", "listen"):
        input_mode = "text"
    with get_conn() as conn:
        exists = conn.execute("SELECT id FROM questions WHERE id = %s", (question_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=400, detail="题目不存在。")
        conn.execute(
            """
            INSERT INTO practice_records (
                user_id, question_id, device_id, input_mode, answer_text,
                judge_mode, score, verdict, judge_result
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                user_id,
                question_id,
                device_id or None,
                input_mode,
                answer_text[:8000],
                judge_mode,
                score_val,
                verdict,
                dump_json(result),
            ),
        )
        mastered = verdict == "过" and (score_val or 0) >= 8
        conn.execute(
            """
            INSERT INTO user_progress (
                user_id, question_id, mastered, last_score, attempt_count, last_practiced_at, updated_at
            ) VALUES (%s, %s, %s, %s, 1, now(), now())
            ON CONFLICT (user_id, question_id) DO UPDATE SET
                mastered = user_progress.mastered OR EXCLUDED.mastered,
                last_score = EXCLUDED.last_score,
                attempt_count = user_progress.attempt_count + 1,
                last_practiced_at = now(),
                updated_at = now()
            """,
            (user_id, question_id, mastered, score_val),
        )


def review_and_weak(user_id: str) -> tuple[list[str], list[str]]:
    with get_conn() as conn:
        review = [
            row["question_id"]
            for row in conn.execute(
                """
                SELECT question_id FROM user_progress
                WHERE user_id = %s AND next_review_at IS NOT NULL AND next_review_at <= now()
                ORDER BY next_review_at
                LIMIT 8
                """,
                (user_id,),
            )
        ]
        weak = [
            row["question_id"]
            for row in conn.execute(
                """
                SELECT question_id FROM user_progress
                WHERE user_id = %s AND last_score IS NOT NULL AND last_score < 6
                ORDER BY last_practiced_at DESC NULLS LAST
                LIMIT 8
                """,
                (user_id,),
            )
        ]
    return review, weak
