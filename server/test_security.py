from __future__ import annotations

import ipaddress
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from starlette.datastructures import Headers


def _request(headers: dict[str, str], host: str = "203.0.113.10"):
    return SimpleNamespace(headers=Headers(headers), client=SimpleNamespace(host=host))


class EgressTest(unittest.TestCase):
    def test_rejects_loopback_link_local_and_rfc1918(self) -> None:
        from server.egress import UnsafeUrl, validate_provider_url

        cases = [
            "http://dashscope.aliyuncs.com/v1",
            "https://localhost/v1",
            "https://127.0.0.1/v1",
            "https://[::1]/v1",
            "https://169.254.169.254/latest/meta-data/",
            "https://10.0.0.8/v1",
            "https://192.168.1.1/v1",
            "https://172.16.0.1/v1",
            "https://evil.example/v1",
        ]
        for url in cases:
            with self.subTest(url=url):
                with self.assertRaises(UnsafeUrl):
                    validate_provider_url(url)

    def test_allows_exact_provider_hosts_after_public_dns(self) -> None:
        from server.egress import validate_provider_url

        def resolve(host: str, *args, **kwargs):
            self.assertIn(host, {"dashscope.aliyuncs.com", "api.deepseek.com"})
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))]

        url = validate_provider_url(
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            resolve=resolve,
        )
        self.assertEqual(url, "https://dashscope.aliyuncs.com/compatible-mode/v1")
        url = validate_provider_url("https://api.deepseek.com/", resolve=resolve)
        self.assertEqual(url, "https://api.deepseek.com")

    def test_dns_rebinding_to_private_ip_is_rejected(self) -> None:
        from server.egress import UnsafeUrl, validate_provider_url

        def resolve(host: str, *args, **kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0)),
            ]

        with self.assertRaises(UnsafeUrl):
            validate_provider_url("https://dashscope.aliyuncs.com/v1", resolve=resolve)

    def test_ipv6_loopback_from_dns_is_rejected(self) -> None:
        from server.egress import UnsafeUrl, validate_provider_url

        def resolve(host: str, *args, **kwargs):
            return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 0, 0, 0))]

        with self.assertRaises(UnsafeUrl):
            validate_provider_url("https://dashscope.aliyuncs.com/v1", resolve=resolve)

    def test_tts_media_rejects_private_and_http(self) -> None:
        from server.egress import UnsafeUrl, validate_tts_media_url

        def private_resolve(host: str, *args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 0))]

        def public_resolve(host: str, *args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.4.4", 0))]

        with self.assertRaises(UnsafeUrl):
            validate_tts_media_url("http://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/a.wav")
        with self.assertRaises(UnsafeUrl):
            validate_tts_media_url(
                "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/a.wav",
                resolve=private_resolve,
            )
        with self.assertRaises(UnsafeUrl):
            validate_tts_media_url("https://evil.example/a.wav", resolve=public_resolve)
        ok = validate_tts_media_url(
            "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/a.wav",
            resolve=public_resolve,
        )
        self.assertTrue(ok.startswith("https://"))

    def test_is_public_ip_blocks_reserved_ranges(self) -> None:
        from server.egress import is_public_ip

        self.assertFalse(is_public_ip("127.0.0.1"))
        self.assertFalse(is_public_ip("::1"))
        self.assertFalse(is_public_ip("169.254.169.254"))
        self.assertFalse(is_public_ip("10.0.0.1"))
        self.assertFalse(is_public_ip("192.168.0.1"))
        self.assertFalse(is_public_ip("172.16.0.1"))
        self.assertFalse(is_public_ip("0.0.0.0"))
        self.assertTrue(is_public_ip("8.8.8.8"))
        self.assertTrue(is_public_ip("1.1.1.1"))
        ipaddress.ip_address("8.8.8.8")


class RateLimitIpTest(unittest.TestCase):
    def test_forged_forwarded_for_is_ignored_without_trusted_proxy(self) -> None:
        from server.rate_limit import client_ip

        req = _request({"x-forwarded-for": "1.2.3.4, 10.0.0.1", "x-real-ip": "8.8.8.8"}, host="203.0.113.10")
        with patch("server.rate_limit.TRUSTED_PROXY_IPS", frozenset()):
            self.assertEqual(client_ip(req), "203.0.113.10")

    def test_trusted_proxy_uses_leftmost_forwarded_for(self) -> None:
        from server.rate_limit import client_ip

        req = _request({"x-forwarded-for": "198.51.100.7, 10.0.0.1"}, host="127.0.0.1")
        with patch("server.rate_limit.TRUSTED_PROXY_IPS", frozenset({"127.0.0.1"})):
            self.assertEqual(client_ip(req), "198.51.100.7")

    def test_untrusted_peer_cannot_spoof_even_if_header_present(self) -> None:
        from server.rate_limit import client_ip

        req = _request({"x-forwarded-for": "198.51.100.7"}, host="198.51.100.99")
        with patch("server.rate_limit.TRUSTED_PROXY_IPS", frozenset({"127.0.0.1"})):
            self.assertEqual(client_ip(req), "198.51.100.99")


class ResumeEncryptionTest(unittest.TestCase):
    def test_production_without_key_refuses_write(self) -> None:
        from server.resume_files import ResumeStorageError, write_stored

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.bin"
            with patch("server.resume_files.RESUME_FILE_KEY", ""), patch(
                "server.resume_files.REQUIRE_RESUME_ENCRYPTION", True
            ):
                with self.assertRaises(ResumeStorageError):
                    write_stored(path, b"%PDF-plain")
                self.assertFalse(path.exists())

    def test_encrypted_read_does_not_silently_return_plaintext(self) -> None:
        from cryptography.fernet import InvalidToken

        from server.resume_files import read_stored, write_stored

        data = b"%PDF-secret"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.bin"
            with patch("server.resume_files.RESUME_FILE_KEY", "unit-test-secret"), patch(
                "server.resume_files.REQUIRE_RESUME_ENCRYPTION", True
            ):
                write_stored(path, data)
                self.assertNotEqual(path.read_bytes(), data)
                path.write_bytes(data)
                with self.assertRaises(InvalidToken):
                    read_stored(path)

    def test_startup_guard_requires_key_in_production(self) -> None:
        from server.resume_files import ResumeStorageError, assert_resume_storage_ready

        with patch("server.resume_files.RESUME_FILE_KEY", ""), patch(
            "server.resume_files.REQUIRE_RESUME_ENCRYPTION", True
        ):
            with self.assertRaises(ResumeStorageError):
                assert_resume_storage_ready()
        with patch("server.resume_files.RESUME_FILE_KEY", "ok"), patch(
            "server.resume_files.REQUIRE_RESUME_ENCRYPTION", True
        ):
            assert_resume_storage_ready()


class HttpClientTest(unittest.TestCase):
    def test_shared_client_does_not_follow_redirects(self) -> None:
        from server import http_client

        http_client.close_http()
        client = http_client.http()
        try:
            self.assertFalse(client.follow_redirects)
        finally:
            http_client.close_http()


if __name__ == "__main__":
    unittest.main()
