from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from server.bank import clear_cache
from server.config import ROOT
from server.db import dump_json, get_conn, init_schema

DUMP_PATH = ROOT / "data" / "public-bank.json"


def dump_public_bank() -> Path:
    script = ROOT / "scripts" / "dump-public-bank.ts"
    if os.name == "nt":
        completed = subprocess.run(f'npx tsx "{script}"', cwd=ROOT, check=False, shell=True)
    else:
        completed = subprocess.run(["npx", "tsx", str(script)], cwd=ROOT, check=False)
    if completed.returncode != 0:
        raise RuntimeError("展开题库失败。请确认已 npm install，然后运行 npx tsx scripts/dump-public-bank.ts")
    if not DUMP_PATH.exists():
        raise RuntimeError(f"没有生成 {DUMP_PATH}")
    return DUMP_PATH


def _flatten(nodes: list[dict[str, Any]], parent_id: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, node in enumerate(nodes):
        rows.append(
            {
                "id": node["id"],
                "parent_id": parent_id,
                "prerequisite_id": node.get("prerequisiteId") or None,
                "level": int(node.get("level") or 1),
                "topic_id": node["topicId"],
                "difficulty": node.get("difficulty") or "基础",
                "angle": node.get("angle") or "",
                "title": node.get("title") or "",
                "question": node.get("question") or "",
                "gist": node.get("gist") or "",
                "points": node.get("points") or [],
                "bonus": node.get("bonus") or "",
                "red_flags": node.get("redFlags") or "",
                "source": node.get("source"),
                "full_answer": node.get("fullAnswer") or "",
                "sort_order": index,
            }
        )
        rows.extend(_flatten(node.get("children") or [], node["id"]))
    return rows


def questions_empty(conn) -> bool:
    row = conn.execute("SELECT COUNT(*) AS n FROM questions").fetchone()
    return int(row["n"] if row else 0) == 0


def seed_payload(payload: dict[str, Any]) -> int:
    topics = payload.get("topics") or []
    questions = payload.get("questions") or []
    if not topics or not questions:
        raise RuntimeError("题库 JSON 里没有主题或题目。")
    rows = _flatten(questions)
    init_schema()
    with get_conn() as conn:
        for topic in topics:
            conn.execute(
                """
                INSERT INTO topics (id, bank, sort_order, name, english, summary, interviewer_note, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (id) DO UPDATE SET
                    bank = EXCLUDED.bank,
                    sort_order = EXCLUDED.sort_order,
                    name = EXCLUDED.name,
                    english = EXCLUDED.english,
                    summary = EXCLUDED.summary,
                    interviewer_note = EXCLUDED.interviewer_note,
                    updated_at = now()
                """,
                (
                    topic["id"],
                    topic.get("bank") or "alibaba",
                    int(topic.get("index") or 0),
                    topic.get("name") or topic["id"],
                    topic.get("english") or "",
                    topic.get("summary") or "",
                    topic.get("interviewerNote") or "",
                ),
            )
        for row in rows:
            source = row["source"] if isinstance(row["source"], dict) else None
            conn.execute(
                """
                INSERT INTO questions (
                    id, parent_id, prerequisite_id, level, topic_id, difficulty, angle,
                    title, question, gist, points, bonus, red_flags, source, full_answer, sort_order,
                    status, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    'published', now()
                )
                ON CONFLICT (id) DO UPDATE SET
                    parent_id = EXCLUDED.parent_id,
                    prerequisite_id = EXCLUDED.prerequisite_id,
                    level = EXCLUDED.level,
                    topic_id = EXCLUDED.topic_id,
                    difficulty = EXCLUDED.difficulty,
                    angle = EXCLUDED.angle,
                    title = EXCLUDED.title,
                    question = EXCLUDED.question,
                    gist = EXCLUDED.gist,
                    points = EXCLUDED.points,
                    bonus = EXCLUDED.bonus,
                    red_flags = EXCLUDED.red_flags,
                    source = EXCLUDED.source,
                    full_answer = EXCLUDED.full_answer,
                    sort_order = EXCLUDED.sort_order,
                    status = 'published',
                    updated_at = now()
                """,
                (
                    row["id"],
                    row["parent_id"],
                    row["prerequisite_id"],
                    row["level"],
                    row["topic_id"],
                    row["difficulty"],
                    row["angle"],
                    row["title"],
                    row["question"],
                    row["gist"],
                    dump_json(row["points"]),
                    row["bonus"],
                    row["red_flags"],
                    dump_json(source) if source else None,
                    row["full_answer"],
                    row["sort_order"],
                ),
            )
        intro = payload.get("ailianIntro") or {"candidate": "", "title": "", "body": ""}
        conn.execute(
            """
            INSERT INTO app_meta (key, value, updated_at) VALUES (%s, %s, now())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            """,
            ("ailian_intro", dump_json(intro)),
        )
        candidate = payload.get("candidate")
        if candidate is not None:
            conn.execute(
                """
                INSERT INTO app_meta (key, value, updated_at) VALUES (%s, %s, now())
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
                """,
                ("candidate", dump_json(candidate)),
            )
    clear_cache()
    return len(rows)


def seed_file(path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path.name} 不是题库对象。")
    return seed_payload(payload)


def bootstrap_if_empty() -> None:
    init_schema()
    with get_conn() as conn:
        empty = questions_empty(conn)
    if not empty:
        return
    if DUMP_PATH.exists():
        count = seed_file(DUMP_PATH)
        print(f"题库为空，已从 {DUMP_PATH.name} 导入 {count} 道题。")
        return
    print("数据库里还没有题库。请运行 npm run seed", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="把展开后的题库写入 PostgreSQL")
    parser.add_argument("--from", dest="source", default="", help="public-bank.json 路径")
    parser.add_argument("--skip-dump", action="store_true", help="不调用 tsx 展开，直接读已有 JSON")
    args = parser.parse_args()
    path = Path(args.source) if args.source else DUMP_PATH
    if not args.skip_dump and not args.source:
        dump_public_bank()
        path = DUMP_PATH
    elif not path.exists():
        raise SystemExit(f"找不到 {path}。去掉 --skip-dump，或先运行 npm run dump-bank。")
    count = seed_file(path)
    stats = json.loads(path.read_text(encoding="utf-8")).get("stats") or {}
    print(f"已写入 {count} 道题（父题 {stats.get('totalParents', '?')}）。")


if __name__ == "__main__":
    main()
