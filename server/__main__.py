from __future__ import annotations

import argparse

import uvicorn

from server.config import HOST, PORT


def main() -> None:
    parser = argparse.ArgumentParser(description="阿里 AI 面试官 Python 服务")
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    uvicorn.run("server.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
