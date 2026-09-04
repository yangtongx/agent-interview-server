from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

from server.config import PG_URI

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
MIGRATIONS_DIR = Path(__file__).with_name("migrations")

_pool: ConnectionPool | None = None


def require_uri() -> str:
    if not PG_URI:
        raise RuntimeError("缺少 PG_URI。请在环境变量或项目根目录 .env 里配置 PostgreSQL 连接串。")
    return PG_URI


def connect() -> psycopg.Connection:
    return psycopg.connect(require_uri(), row_factory=dict_row, autocommit=False)


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=require_uri(),
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row, "autocommit": False},
            open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def get_conn():
    with get_pool().connection() as conn:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _sql_statements(sql: str) -> list[str]:
    statements: list[str] = []
    buf: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buf.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(buf).strip().rstrip(";")
            if stmt:
                statements.append(stmt)
            buf = []
    tail = "\n".join(buf).strip().rstrip(";")
    if tail:
        statements.append(tail)
    return statements


def apply_migrations(conn: psycopg.Connection | None = None) -> list[str]:
    owns = conn is None
    if owns:
        conn = connect()
    applied: list[str] = []
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              id text PRIMARY KEY,
              applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        done = {row["id"] for row in conn.execute("SELECT id FROM schema_migrations")}
        existing = conn.execute("SELECT to_regclass('public.questions') AS t").fetchone()
        has_questions = bool(existing and existing["t"])
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            mid = path.stem
            if mid in done:
                continue
            if mid == "001_init" and has_questions:
                conn.execute("INSERT INTO schema_migrations (id) VALUES (%s)", (mid,))
                applied.append(mid)
                continue
            sql = path.read_text(encoding="utf-8")
            for stmt in _sql_statements(sql):
                conn.execute(stmt)
            conn.execute("INSERT INTO schema_migrations (id) VALUES (%s)", (mid,))
            applied.append(mid)
            has_questions = True
        conn.commit()
        return applied
    except Exception:
        conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


def init_schema(conn: psycopg.Connection | None = None) -> None:
    apply_migrations(conn)


def dump_json(value: object) -> Json:
    return Json(value)
