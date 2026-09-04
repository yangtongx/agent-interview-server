from __future__ import annotations

from server.db import apply_migrations, get_conn


def main() -> None:
    apply_migrations()
    with get_conn() as conn:
        tables = [
            row["table_name"]
            for row in conn.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """
            )
        ]
        print("tables", tables)
        for name in ("topics", "questions", "app_meta", "users", "learning_paths", "question_labels"):
            if name in tables:
                count = conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()
                print(name, int(count["n"] if count else 0))


if __name__ == "__main__":
    main()
