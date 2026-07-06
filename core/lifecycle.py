import logging

import discord
from discord.ext import commands, tasks

from core.audit_log_repository import delete_guild_logs

logger = logging.getLogger(__name__)

_cleanup_handlers: list = []
_guild_remove_handlers: list = []


def register_cleanup_handler(handler) -> None:
    """
    註冊一個定期背景清理函式，Lifecycle 的清理任務會依序呼叫。
    各功能自己的清理邏輯留在自己的檔案，這裡只負責依序呼叫，不了解各功能的內部實作。

    Args:
        handler: 無參數的 async function
    """
    _cleanup_handlers.append(handler)


def register_guild_remove_handler(handler) -> None:
    """
    註冊一個機器人被移出伺服器時要執行的清理函式。

    Args:
        handler: 接受 guild_id: int 參數的 async function
    """
    _guild_remove_handlers.append(handler)


class Lifecycle(commands.Cog):
    """
    依序呼叫各功能透過 register_cleanup_handler()／register_guild_remove_handler()
    註冊的清理函式，本身不了解各功能的內部清理邏輯或持久化 View（各功能自己在 cog_load() 註冊）。
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        """
        載入 Cog 時啟動清理背景任務。
        """
        self.cleanup_task.start()

    def cog_unload(self) -> None:
        """
        卸載 Cog 時停止清理背景任務。
        """
        self.cleanup_task.cancel()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """
        機器人被移出伺服器時，立即清除該伺服器的稽核紀錄，並依序呼叫各功能註冊的伺服器移除清理函式。

        Args:
            guild: 被移出的伺服器物件
        """
        deleted_count = await delete_guild_logs(guild.id)
        if deleted_count:
            print(f"[資訊] 已移出伺服器 {guild.id}，清除 {deleted_count} 筆稽核紀錄。")

        for handler in _guild_remove_handlers:
            try:
                await handler(guild.id)
            except Exception as error:
                logger.error(f"執行伺服器移除清理函式失敗：{error}", exc_info=True)

    # ---------------------------------------------------
    # 背景任務：依序呼叫各功能註冊的清理函式 (每 12 小時檢查一次)
    # ---------------------------------------------------
    @tasks.loop(hours=12)
    async def cleanup_task(self) -> None:
        """
        定期依序呼叫各功能透過 register_cleanup_handler() 註冊的清理函式，
        實際清理邏輯留在各功能自己的檔案，這裡只負責呼叫與錯誤隔離（單一函式失敗不影響其他函式）。
        """
        await self.bot.wait_until_ready()
        print("[背景任務] 開始執行定期清理任務...")

        for handler in _cleanup_handlers:
            try:
                await handler()
            except Exception as error:
                logger.error(f"執行定期清理函式失敗：{error}", exc_info=True)

        print("[背景任務] 清理任務結束。")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Lifecycle(bot))

