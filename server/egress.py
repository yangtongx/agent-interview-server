from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlparse

from server.config import LLM_ALLOWED_HOSTS

ResolveFn = Callable[..., list]

TTS_MEDIA_SUFFIXES = (".aliyuncs.com", ".alicdn.com")


class UnsafeUrl(ValueError):
    pass


def is_public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _hostname(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() != "https":
        raise UnsafeUrl("只允许 HTTPS 接口地址。")
    if parsed.username or parsed.password:
        raise UnsafeUrl("接口地址无效。")
    host = (parsed.hostname or "").lower().strip()
    if not host:
        raise UnsafeUrl("接口地址无效。")
    if parsed.port not in (None, 443):
        raise UnsafeUrl("接口地址无效。")
    return host


def _resolved_ips(host: str, resolve: ResolveFn) -> set[str]:
    try:
        infos = resolve(host, None)
    except OSError as err:
        raise UnsafeUrl("无法解析接口地址。") from err
    ips: set[str] = set()
    for info in infos or []:
        sockaddr = info[4] if len(info) > 4 else None
        if sockaddr and sockaddr[0]:
            ips.add(str(sockaddr[0]))
    if not ips:
        raise UnsafeUrl("无法解析接口地址。")
    return ips


def _assert_public_host(host: str, resolve: ResolveFn) -> None:
    try:
        ipaddress.ip_address(host)
        if not is_public_ip(host):
            raise UnsafeUrl("接口地址不被允许。")
        return
    except ValueError:
        pass
    for ip in _resolved_ips(host, resolve):
        if not is_public_ip(ip):
            raise UnsafeUrl("接口地址不被允许。")


def _host_allowed(host: str, allowed_hosts: frozenset[str], suffixes: tuple[str, ...] = ()) -> bool:
    if host in allowed_hosts:
        return True
    return any(host.endswith(suffix) for suffix in suffixes)


def _normalize(url: str) -> str:
    parsed = urlparse(url.strip())
    path = parsed.path.rstrip("/")
    query = f"?{parsed.query}" if parsed.query else ""
    return f"https://{parsed.hostname.lower()}{path}{query}"


def validate_https_url(
    url: str,
    *,
    allowed_hosts: frozenset[str] | None = None,
    allowed_suffixes: tuple[str, ...] = (),
    resolve: ResolveFn = socket.getaddrinfo,
) -> str:
    host = _hostname(url)
    allowed = allowed_hosts if allowed_hosts is not None else frozenset()
    if allowed or allowed_suffixes:
        if not _host_allowed(host, allowed, allowed_suffixes):
            raise UnsafeUrl("接口地址不被允许。")
    _assert_public_host(host, resolve)
    return _normalize(url)


def validate_provider_url(url: str, *, resolve: ResolveFn = socket.getaddrinfo) -> str:
    return validate_https_url(url, allowed_hosts=LLM_ALLOWED_HOSTS, resolve=resolve)


def validate_tts_media_url(url: str, *, resolve: ResolveFn = socket.getaddrinfo) -> str:
    return validate_https_url(
        url,
        allowed_hosts=LLM_ALLOWED_HOSTS,
        allowed_suffixes=TTS_MEDIA_SUFFIXES,
        resolve=resolve,
    )
