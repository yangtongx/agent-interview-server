from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from server.config import WEB_ROOT
from server.db import apply_migrations, close_pool
from server.http_client import close_async_http, close_http
from server.observability import RequestContextMiddleware, setup_logging
from server.resume_files import assert_resume_storage_ready
from server.routers import admin, ai, auth, bank, health, resumes
from server.seed import bootstrap_if_empty


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    assert_resume_storage_ready()
    apply_migrations()
    bootstrap_if_empty()
    yield
    close_http()
    await close_async_http()
    close_pool()


app = FastAPI(title="Agent 面试舱", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health.router)
app.include_router(bank.router)
app.include_router(auth.router)
app.include_router(ai.router)
app.include_router(resumes.router)
app.include_router(admin.router)


@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException):
    detail = exc.detail
    message = detail if isinstance(detail, str) else "请求无效。"
    return JSONResponse({"error": message}, status_code=exc.status_code)


web_root = Path(WEB_ROOT) if WEB_ROOT else None
if web_root and web_root.is_dir():
    app.mount("/", StaticFiles(directory=web_root, html=True), name="web")
