import socket
from unittest.mock import AsyncMock

import pytest

from features.link_checker.cog import LinkChecker
from features.link_checker.url_safety import (
    PublicAddressResolver,
    is_public_ip_address,
    is_safe_public_url,
)


class FakeResolver:
    """提供 PublicAddressResolver 測試所需的固定 DNS 結果。"""

    def __init__(self, addresses: list[str]) -> None:
        self.addresses = addresses

    async def resolve(self, host: str, port: int, family: int) -> list[dict[str, object]]:
        """回傳預先指定的 DNS 位址。"""
        return [
            {
                "hostname": host,
                "host": address,
                "port": port,
                "family": family,
                "proto": 0,
                "flags": 0,
            }
            for address in self.addresses
        ]

    async def close(self) -> None:
        """模擬關閉 resolver。"""


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("8.8.8.8", True),
        ("127.0.0.1", False),
        ("10.0.0.1", False),
        ("169.254.169.254", False),
        ("::1", False),
        ("::ffff:127.0.0.1", False),
    ],
)
def test_is_public_ip_address(address: str, expected: bool) -> None:
    """只允許可在公網路由的 IP 位址。"""
    assert is_public_ip_address(address) is expected


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://user:password@example.com/",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "https://example.com:8443/",
    ],
)
def test_is_safe_public_url_rejects_unsafe_targets(url: str) -> None:
    """拒絕非 HTTP、內網、帳密與非標準連接埠 URL。"""
    assert is_safe_public_url(url) is False


@pytest.mark.asyncio
async def test_public_address_resolver_filters_private_results() -> None:
    """DNS 同時回傳公開與私有位址時，只能把公開位址交給 aiohttp。"""
    resolver = PublicAddressResolver()
    resolver._resolver = FakeResolver(["10.0.0.1", "8.8.8.8"])

    results = await resolver.resolve("example.com", 443, socket.AF_INET)

    assert [result["host"] for result in results] == ["8.8.8.8"]


@pytest.mark.asyncio
async def test_unshorten_url_stops_before_private_redirect() -> None:
    """短網址若重新導向到私有位址，應回傳原始網址且不得請求私有目標。"""
    checker = object.__new__(LinkChecker)
    checker.shortener_domains = {"short.example"}
    checker._request_redirect = AsyncMock(
        return_value=(302, "http://127.0.0.1/internal")
    )

    result = await checker.unshorten_url("https://short.example/link")

    assert result == "https://short.example/link"
    checker._request_redirect.assert_awaited_once_with("https://short.example/link")
