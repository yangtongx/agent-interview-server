from __future__ import annotations

import time

from server.config import TRUSTED_PROXY_IPS


class RateLimiter:
    def __init__(self, limit: int, window_ms: int = 60_000) -> None:
        self.limit = limit
        self.window_ms = window_ms
        self.hits: dict[str, tuple[int, float]] = {}

    def too_many(self, ip: str) -> bool:
        now = time.time() * 1000
        row = self.hits.get(ip)
        if not row or now > row[1]:
            self.hits[ip] = (1, now + self.window_ms)
            return False
        count, reset_at = row
        count += 1
        self.hits[ip] = (count, reset_at)
        return count > self.limit


def client_ip(request) -> str:
    peer = ""
    if request.client and getattr(request.client, "host", None):
        peer = str(request.client.host)
    if peer in TRUSTED_PROXY_IPS:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip() or peer or "local"
        real_ip = (request.headers.get("x-real-ip") or "").strip()
        if real_ip:
            return real_ip
    return peer or "local"
