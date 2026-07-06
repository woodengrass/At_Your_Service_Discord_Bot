import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

import aiohttp
from aiohttp.abc import AbstractResolver


class PublicAddressResolver(AbstractResolver):
    """只回傳公開 IP 位址，避免 HTTP 用戶端連線到內部網路。"""

    def __init__(self) -> None:
        self._resolver = aiohttp.DefaultResolver()

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: int = socket.AF_INET,
    ) -> list[dict[str, Any]]:
        """
        解析主機名稱並過濾所有非公開 IP 位址。

        Args:
            host: 要解析的主機名稱
            port: 目標連接埠
            family: 位址家族

        Returns:
            aiohttp 可用的公開位址解析結果

        Raises:
            OSError: 找不到公開位址時拋出
        """
        results = await self._resolver.resolve(host, port, family)
        public_results = [result for result in results if is_public_ip_address(result["host"])]
        if not public_results:
            raise OSError(f"主機沒有可用的公開 IP 位址：{host}")
        return public_results

    async def close(self) -> None:
        """關閉底層 DNS resolver。"""
        await self._resolver.close()


def is_public_ip_address(address: str) -> bool:
    """
    判斷位址是否為可安全連線的公開 IP，並正確處理 IPv4-mapped IPv6。

    Args:
        address: IPv4 或 IPv6 位址字串

    Returns:
        True 表示位址可在公網路由
    """
    try:
        ip_address = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip_address, ipaddress.IPv6Address) and ip_address.ipv4_mapped is not None:
        ip_address = ip_address.ipv4_mapped
    return ip_address.is_global


def is_safe_public_url(url: str) -> bool:
    """
    驗證短網址展開流程可連線的 URL 基本結構。

    Args:
        url: 待驗證網址

    Returns:
        True 表示使用 HTTP/HTTPS、無帳密且使用標準連接埠
    """
    try:
        parsed_url = urlparse(url)
        port = parsed_url.port
    except ValueError:
        return False
    if parsed_url.scheme.lower() not in {"http", "https"}:
        return False
    if not parsed_url.hostname or parsed_url.username is not None or parsed_url.password is not None:
        return False
    try:
        ipaddress.ip_address(parsed_url.hostname)
    except ValueError:
        pass
    else:
        if not is_public_ip_address(parsed_url.hostname):
            return False
    expected_port = 80 if parsed_url.scheme.lower() == "http" else 443
    return port is None or port == expected_port
