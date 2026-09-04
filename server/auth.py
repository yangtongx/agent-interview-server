from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request

from server.config import SESSION_DAYS
from server.db import get_conn, dump_json

PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
LOCAL_RE = re.compile(r"^[A-Za-z0-9_]{4,32}$")
DEVICE_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hash_password(password: str) -> dict[str, Any]:
    salt = secrets.token_hex(16)
    rounds = 210_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), rounds)
    return {"algo": "pbkdf2_sha256", "salt": salt, "hash": digest.hex(), "rounds": rounds}


def verify_password(password: str, extra: Any) -> bool:
    if not isinstance(extra, dict):
        extra = {}
    salt = extra.get("salt")
    stored = extra.get("hash")
    rounds = extra.get("rounds") or 210_000
    if not isinstance(salt, str) or not isinstance(stored, str):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(rounds))
    return secrets.compare_digest(digest.hex(), stored)


def issue_token() -> str:
    return secrets.token_urlsafe(32)


def _as_str(value: Any) -> str:
    if isinstance(value, UUID):
        return str(value)
    return str(value) if value is not None else ""


def public_user(
    row: dict[str, Any],
    profile: dict[str, Any] | None = None,
    is_guest: bool = False,
) -> dict[str, Any]:
    payload = {
        "id": _as_str(row["id"]),
        "displayName": row.get("display_name") or "",
        "avatarUrl": row.get("avatar_url"),
        "audience": row.get("audience") or "unset",
        "status": row.get("status") or "active",
        "isGuest": bool(is_guest),
    }
    if profile:
        payload["profile"] = {
            "experienceYears": profile.get("experience_years"),
            "jobRole": profile.get("job_role") or "",
            "targetRole": profile.get("target_role") or "",
            "domains": list(profile.get("domains") or []),
            "bio": profile.get("bio") or "",
        }
    return payload


def bearer_token(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


def user_from_token(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    digest = hash_token(token)
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT u.*, s.id AS session_id, s.device_id, s.expires_at, s.revoked_at
            FROM auth_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.refresh_token_hash = %s
            """,
            (digest,),
        ).fetchone()
        if not row:
            return None
        if row.get("revoked_at") or row["expires_at"] <= _now() or row.get("status") != "active":
            return None
        profile = conn.execute(
            "SELECT * FROM user_profiles WHERE user_id = %s", (row["id"],)
        ).fetchone()
        user = public_user(row, profile, is_guest=not _has_account_identity(conn, _as_str(row["id"])))
        user["sessionId"] = _as_str(row["session_id"])
        user["deviceId"] = _as_str(row["device_id"]) if row.get("device_id") else ""
        return user


def optional_user(request: Request) -> dict[str, Any] | None:
    return user_from_token(bearer_token(request))


def require_user(request: Request) -> dict[str, Any]:
    user = optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录或登录已过期。")
    return user


def upsert_device(conn, device_id: str | None, platform: str, user_id: str | None, name: str = "") -> str | None:
    if not device_id:
        return None
    if not DEVICE_RE.match(device_id):
        raise HTTPException(status_code=400, detail="deviceId 必须是 UUID。")
    if platform not in ("harmony", "web", "android", "ios"):
        platform = "web"
    existing = conn.execute("SELECT id, user_id FROM devices WHERE id = %s", (device_id,)).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE devices
            SET platform = %s, device_name = COALESCE(NULLIF(%s, ''), device_name),
                user_id = COALESCE(%s, user_id), last_seen_at = now()
            WHERE id = %s
            """,
            (platform, name, user_id, device_id),
        )
        return _as_str(existing["id"])
    conn.execute(
        """
        INSERT INTO devices (id, user_id, platform, device_name, last_seen_at)
        VALUES (%s, %s, %s, %s, now())
        """,
        (device_id, user_id, platform, name),
    )
    return device_id


def _ensure_free_membership(conn, user_id: str) -> None:
    conn.execute(
        """
        INSERT INTO memberships (user_id, plan_id, status, source, updated_at)
        VALUES (%s, 'free', 'active', 'none', now())
        ON CONFLICT (user_id) DO NOTHING
        """,
        (user_id,),
    )
    conn.execute(
        """
        INSERT INTO user_profiles (user_id, updated_at)
        VALUES (%s, now())
        ON CONFLICT (user_id) DO NOTHING
        """,
        (user_id,),
    )


def _create_user(conn, display_name: str = "", audience: str = "unset") -> str:
    if audience not in ("unset", "transition", "experienced"):
        audience = "unset"
    row = conn.execute(
        """
        INSERT INTO users (display_name, audience, status, updated_at)
        VALUES (%s, %s, 'active', now())
        RETURNING id
        """,
        (display_name, audience),
    ).fetchone()
    user_id = _as_str(row["id"])
    _ensure_free_membership(conn, user_id)
    return user_id


def _create_session(conn, user_id: str, device_id: str | None) -> str:
    raw = issue_token()
    expires = _now() + timedelta(days=SESSION_DAYS)
    conn.execute(
        """
        INSERT INTO auth_sessions (user_id, device_id, refresh_token_hash, expires_at)
        VALUES (%s, %s, %s, %s)
        """,
        (user_id, device_id or None, hash_token(raw), expires),
    )
    return raw


def session_payload(user: dict[str, Any], token: str) -> dict[str, Any]:
    return {"token": token, "user": user}


def login_device(device_id: str, platform: str, device_name: str = "", audience: str = "unset") -> dict[str, Any]:
    uid = f"device:{device_id}"
    with get_conn() as conn:
        ident = conn.execute(
            "SELECT user_id FROM auth_identities WHERE provider = 'local' AND provider_uid = %s",
            (uid,),
        ).fetchone()
        if ident:
            user_id = _as_str(ident["user_id"])
            if _has_account_identity(conn, user_id):
                conn.execute(
                    "DELETE FROM auth_identities WHERE provider = 'local' AND provider_uid = %s",
                    (uid,),
                )
                ident = None
        if not ident:
            user_id = _create_user(conn, display_name="访客", audience=audience)
            conn.execute(
                """
                INSERT INTO auth_identities (user_id, provider, provider_uid, extra)
                VALUES (%s, 'local', %s, '{}'::jsonb)
                """,
                (user_id, uid),
            )
        upsert_device(conn, device_id, platform, user_id, device_name)
        token = _create_session(conn, user_id, device_id)
        row = conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
        profile = conn.execute("SELECT * FROM user_profiles WHERE user_id = %s", (user_id,)).fetchone()
        return session_payload(public_user(row, profile, is_guest=True), token)


def register_account(
    provider: str,
    uid: str,
    password: str,
    display_name: str,
    platform: str,
    device_id: str | None,
    audience: str = "unset",
) -> dict[str, Any]:
    provider, uid = _normalize_identity(provider, uid)
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位。")
    extra = hash_password(password)
    with get_conn() as conn:
        exists = conn.execute(
            "SELECT user_id FROM auth_identities WHERE provider = %s AND provider_uid = %s",
            (provider, uid),
        ).fetchone()
        if exists:
            raise HTTPException(status_code=409, detail="这个账号已经注册过了。")
        user_id = None
        if device_id:
            device_ident = conn.execute(
                "SELECT user_id FROM auth_identities WHERE provider = 'local' AND provider_uid = %s",
                (f"device:{device_id}",),
            ).fetchone()
            if device_ident:
                user_id = _as_str(device_ident["user_id"])
        if not user_id:
            user_id = _create_user(conn, display_name=display_name, audience=audience)
        elif display_name:
            conn.execute(
                "UPDATE users SET display_name = %s, updated_at = now() WHERE id = %s",
                (display_name, user_id),
            )
        conn.execute(
            """
            INSERT INTO auth_identities (user_id, provider, provider_uid, extra)
            VALUES (%s, %s, %s, %s)
            """,
            (user_id, provider, uid, dump_json(extra)),
        )
        upsert_device(conn, device_id, platform, user_id)
        _drop_device_login_identity(conn, device_id)
        token = _create_session(conn, user_id, device_id)
        row = conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
        profile = conn.execute("SELECT * FROM user_profiles WHERE user_id = %s", (user_id,)).fetchone()
        return session_payload(public_user(row, profile, is_guest=False), token)


def login_account(
    provider: str,
    uid: str,
    password: str,
    platform: str,
    device_id: str | None,
) -> dict[str, Any]:
    provider, uid = _normalize_identity(provider, uid)
    with get_conn() as conn:
        ident = conn.execute(
            "SELECT user_id, extra FROM auth_identities WHERE provider = %s AND provider_uid = %s",
            (provider, uid),
        ).fetchone()
        if not ident or not verify_password(password, ident.get("extra")):
            raise HTTPException(status_code=401, detail="账号或密码不对。")
        user_id = _as_str(ident["user_id"])
        upsert_device(conn, device_id, platform, user_id)
        _drop_device_login_identity(conn, device_id)
        token = _create_session(conn, user_id, device_id)
        row = conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
        if row.get("status") != "active":
            raise HTTPException(status_code=403, detail="账号已停用。")
        profile = conn.execute("SELECT * FROM user_profiles WHERE user_id = %s", (user_id,)).fetchone()
        return session_payload(public_user(row, profile, is_guest=False), token)


def load_me(user_id: str) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
        profile = conn.execute("SELECT * FROM user_profiles WHERE user_id = %s", (user_id,)).fetchone()
        return public_user(row, profile, is_guest=not _has_account_identity(conn, user_id))


def update_me(user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    audience = body.get("audience")
    display_name = body.get("displayName")
    profile_in = body.get("profile") if isinstance(body.get("profile"), dict) else {}
    with get_conn() as conn:
        if audience in ("unset", "transition", "experienced"):
            conn.execute(
                "UPDATE users SET audience = %s, updated_at = now() WHERE id = %s",
                (audience, user_id),
            )
        if isinstance(display_name, str):
            conn.execute(
                "UPDATE users SET display_name = %s, updated_at = now() WHERE id = %s",
                (display_name.strip()[:40], user_id),
            )
        if profile_in:
            years = profile_in.get("experienceYears")
            years_val = None
            if isinstance(years, bool):
                years_val = None
            elif isinstance(years, (int, float)):
                years_val = int(years)
            conn.execute(
                """
                INSERT INTO user_profiles (user_id, experience_years, job_role, target_role, domains, bio, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (user_id) DO UPDATE SET
                    experience_years = COALESCE(EXCLUDED.experience_years, user_profiles.experience_years),
                    job_role = COALESCE(EXCLUDED.job_role, user_profiles.job_role),
                    target_role = COALESCE(EXCLUDED.target_role, user_profiles.target_role),
                    domains = COALESCE(EXCLUDED.domains, user_profiles.domains),
                    bio = COALESCE(EXCLUDED.bio, user_profiles.bio),
                    updated_at = now()
                """,
                (
                    user_id,
                    years_val,
                    str(profile_in.get("jobRole") or "")[:40],
                    str(profile_in.get("targetRole") or "")[:40],
                    list(profile_in.get("domains") or []),
                    str(profile_in.get("bio") or "")[:400],
                ),
            )
        row = conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
        profile = conn.execute("SELECT * FROM user_profiles WHERE user_id = %s", (user_id,)).fetchone()
        return public_user(row, profile, is_guest=not _has_account_identity(conn, user_id))


def revoke_session(token: str) -> None:
    if not token:
        return
    with get_conn() as conn:
        conn.execute(
            "UPDATE auth_sessions SET revoked_at = now() WHERE refresh_token_hash = %s AND revoked_at IS NULL",
            (hash_token(token),),
        )


def _has_account_identity(conn, user_id: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM auth_identities
        WHERE user_id = %s
          AND NOT (provider = 'local' AND provider_uid LIKE %s)
        LIMIT 1
        """,
        (user_id, "device:%"),
    ).fetchone()
    return bool(row)


def _drop_device_login_identity(conn, device_id: str | None) -> None:
    if not device_id:
        return
    conn.execute(
        "DELETE FROM auth_identities WHERE provider = 'local' AND provider_uid = %s",
        (f"device:{device_id}",),
    )


def _normalize_identity(provider: str, uid: str) -> tuple[str, str]:
    provider = (provider or "").strip()
    uid = (uid or "").strip()
    if provider == "phone":
        if not PHONE_RE.match(uid):
            raise HTTPException(status_code=400, detail="手机号格式不对。")
        return provider, uid
    if provider == "local":
        if uid.startswith("device:"):
            raise HTTPException(status_code=400, detail="请用自己的用户名注册。")
        if not LOCAL_RE.match(uid):
            raise HTTPException(status_code=400, detail="用户名用 4～32 位字母数字或下划线。")
        return provider, uid.lower()
    raise HTTPException(status_code=400, detail="目前支持手机号或用户名登录。")
