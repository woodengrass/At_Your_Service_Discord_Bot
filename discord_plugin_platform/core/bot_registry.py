"""
儲存目前執行中的 discord.py Client/Bot 實例，供不直接持有 bot 參照的模組
（例如 sandbox/worker.py）反查用。

背景：sandbox/worker.py 刻意跟 bot_integration/、core/dispatcher.py 保持低耦合
（Track A 的沙箱引擎本身不該依賴 Discord bot 的生命週期怎麼管理），但
ExecutionContext（core/capability_api.py）建立時需要一個 discord.Client
給能力函式反查 guild/member/channel。與其把 bot 實例一路從
bot_integration/listeners.py 經 core/dispatcher.py 傳進 sandbox/worker.py
（會牽動 Track D／Track E 正在處理的呼叫鏈簽名），不如比照
core/database.py 的單例連線模式，另外開一個模組級單例讓 worker.py 自己反查，
呼叫端（dispatcher.py）完全不用改動。

bot_integration 的 Cog `setup(bot)` 啟動時要呼叫一次 `set_bot(bot)`，
之後任何地方都可以呼叫 `get_bot()` 取得同一個實例。
"""

import discord

_bot: discord.Client | None = None


def set_bot(bot: discord.Client) -> None:
    """
    註冊目前執行中的 discord.py Client/Bot 實例，機器人啟動時呼叫一次。

    Args:
        bot: discord.py 的 Client 或 Bot 實例
    """
    global _bot
    _bot = bot


def get_bot() -> discord.Client:
    """
    取得已註冊的 discord.py Client/Bot 實例。

    Returns:
        目前執行中的 discord.py Client/Bot 實例

    Raises:
        RuntimeError: 尚未呼叫過 set_bot() 註冊
    """
    if _bot is None:
        raise RuntimeError("尚未註冊 bot 實例，請先呼叫 set_bot()")
    return _bot
