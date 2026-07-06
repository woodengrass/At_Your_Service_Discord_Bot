import logging
import os
import re
import time
from urllib.parse import urljoin, urlparse

import aiohttp
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from core.guild_settings import GuildSettings
from core.i18n import i18n
from features.link_checker.repository import get_all_keywords, seed_default_keywords_if_empty
from features.link_checker.url_safety import PublicAddressResolver, is_safe_public_url

logger = logging.getLogger(__name__)

load_dotenv("token.env")

GOOGLE_API_KEY = os.getenv("GOOGLE_SAFE_BROWSING_KEY")
MAX_SHORT_URL_REDIRECTS = 3
REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


class LinkChecker(commands.Cog):
    """
    偵測訊息中的網址，透過關鍵字黑名單與 Google Safe Browsing API 檢查是否為惡意連結。
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        self.url_pattern = re.compile(r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+(?:/[-\w./?%&=]*)?')

        self.shortener_domains = {
            "bit.ly", "tinyurl.com", "goo.gl", "rebrand.ly", "t.co", "is.gd",
            "buff.ly", "adf.ly", "ow.ly", "j.mp", "su.pr", "bc.vc", "zz.gd"
        }

        self.cache: dict[str, tuple[bool, float]] = {}
        self.cache_ttl = 3600  # 快取存活 1 小時
        self.session: aiohttp.ClientSession | None = None
        self.suspicious_keywords: list[str] = []

        self.clean_cache_task.start()

    async def cog_load(self) -> None:
        """
        載入 Cog 時建立共用的 aiohttp session，並從資料庫載入可疑關鍵字清單。
        """
        connector = aiohttp.TCPConnector(resolver=PublicAddressResolver(), ttl_dns_cache=0)
        timeout = aiohttp.ClientTimeout(total=5, connect=3, sock_read=3)
        self.session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        await seed_default_keywords_if_empty()
        await self.reload_keywords()

    async def reload_keywords(self) -> None:
        """
        從資料庫重新載入可疑關鍵字快取，新增/刪除關鍵字後呼叫可立即生效。
        """
        self.suspicious_keywords = await get_all_keywords()

    async def cog_unload(self) -> None:
        """
        卸載 Cog 時停止快取清理背景任務並關閉共用的 aiohttp session。
        """
        self.clean_cache_task.cancel()
        if self.session:
            await self.session.close()

    @tasks.loop(hours=1)
    async def clean_cache_task(self) -> None:
        """
        定期清除已超過存活時間的網址安全性快取。
        """
        now = time.time()
        expired_keys = [
            cache_key
            for cache_key, cache_value in self.cache.items()
            if now - cache_value[1] > self.cache_ttl
        ]
        for cache_key in expired_keys:
            del self.cache[cache_key]

    def is_module_enabled(self, guild_id: int) -> bool:
        """
        檢查指定伺服器是否已啟用連結檢查功能。

        Args:
            guild_id: 伺服器 ID

        Returns:
            True 表示已啟用
        """
        config = GuildSettings.get_module_config(guild_id, "link_checker")
        return config.get("enabled", False)

    async def unshorten_url(self, url: str) -> str:
        """
        若網址屬於已知短網址服務，還原為原始網址。

        Args:
            url: 原始網址

        Returns:
            還原後的網址；若非短網址或還原失敗則回傳原網址
        """
        parsed_url = urlparse(url)
        domain = parsed_url.hostname.lower() if parsed_url.hostname else ""
        if domain not in self.shortener_domains:
            return url

        current_url = url
        try:
            for _redirect_count in range(MAX_SHORT_URL_REDIRECTS + 1):
                if not is_safe_public_url(current_url):
                    logger.warning("拒絕展開不安全的短網址目標：%s", current_url)
                    return url

                status, location = await self._request_redirect(current_url)
                if status not in REDIRECT_STATUS_CODES or not location:
                    return current_url
                current_url = urljoin(current_url, location)

            logger.warning("短網址重新導向次數超過上限：%s", url)
        except Exception as error:
            logger.error(f"短網址還原失敗：{error}", exc_info=True)
        return url

    async def _request_redirect(self, url: str) -> tuple[int, str | None]:
        """
        對單一 URL 發出不自動跟隨的輕量請求，回傳狀態碼與 Location。

        Args:
            url: 已通過基本結構驗證的網址

        Returns:
            HTTP 狀態碼與 Location 標頭；沒有 Location 時回傳 None
        """
        if self.session is None:
            raise RuntimeError("HTTP session 尚未初始化")
        async with self.session.head(url, allow_redirects=False) as response:
            if response.status not in {405, 501}:
                return response.status, response.headers.get("Location")

        headers = {"Range": "bytes=0-0"}
        async with self.session.get(url, headers=headers, allow_redirects=False) as response:
            return response.status, response.headers.get("Location")

    async def check_google_safe_browsing(self, url: str) -> bool:
        """
        呼叫 Google Safe Browsing API 檢查網址是否為已知威脅。
        Google 已將 v4 的 threatMatches:find 方法標示為棄用，改用 v5alpha1 的 urls:search，
        此端點目前仍是 Google 標示的 Alpha 版本，格式未來仍可能調整。

        Args:
            url: 欲檢查的網址

        Returns:
            True 表示安全；若 API 未設定或呼叫失敗則預設回傳 True
        """
        if not GOOGLE_API_KEY:
            return True

        api_url = "https://safebrowsing.googleapis.com/v5alpha1/urls:search"
        params = {"key": GOOGLE_API_KEY, "urls[]": url}

        try:
            async with self.session.get(api_url, params=params, timeout=3) as response:
                if response.status == 200:
                    data = await response.json()

                    if data.get("threats"):
                        return False
                else:
                    logger.error(f"Google Safe Browsing API 回應錯誤：{response.status}")
        except Exception as e:
            logger.error(f"Google Safe Browsing API 請求失敗：{e}", exc_info=True)

        return True

    async def check_url_safety(self, url: str) -> bool:
        """
        綜合快取、短網址還原、關鍵字黑名單與 Google Safe Browsing API 檢查網址安全性。

        Args:
            url: 欲檢查的網址

        Returns:
            True 表示安全
        """
        if url in self.cache:
            is_safe, timestamp = self.cache[url]
            if time.time() - timestamp < self.cache_ttl:
                return is_safe

        final_url = await self.unshorten_url(url)
        final_url_lower = final_url.lower()

        is_safe = True

        for keyword in self.suspicious_keywords:
            if keyword in final_url_lower:
                is_safe = False
                break

        if is_safe:
            is_safe = await self.check_google_safe_browsing(final_url)

        self.cache[url] = (is_safe, time.time())
        if final_url != url:
            self.cache[final_url] = (is_safe, time.time())

        return is_safe

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """
        監聽訊息，檢查其中網址的安全性並依結果加上反應或發送警告。

        Args:
            message: 收到的訊息物件
        """
        if message.author.bot or not message.guild:
            return

        if not self.is_module_enabled(message.guild.id):
            return

        urls = self.url_pattern.findall(message.content)
        if not urls:
            return

        has_unsafe_link = False
        unsafe_links = []

        for url in urls:
            is_safe = await self.check_url_safety(url)

            if not is_safe:
                has_unsafe_link = True
                unsafe_links.append(url)
        if has_unsafe_link:
            try:
                warning_message = i18n.get_text("messages.link_unsafe_warning", message.guild.id, url=unsafe_links[0])
                await message.reply(warning_message, mention_author=True)
            except Exception as e:
                logger.error(f"發送惡意連結警告失敗：{e}", exc_info=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LinkChecker(bot))

