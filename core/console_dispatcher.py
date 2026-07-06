import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable

from discord.ext import commands

logger = logging.getLogger(__name__)

ConsoleHandler = Callable[[str], Awaitable[None]]

_handlers: list[ConsoleHandler] = []
_console_task: asyncio.Task | None = None


def register_handler(handler: ConsoleHandler) -> None:
    """
    註冊一個終端機指令處理函式，讓多個 Cog 可以共用同一個終端機輸入來源，
    避免各自獨立監聽 stdin 導致同一行輸入被隨機分配給錯的處理函式。

    Args:
        handler: async def handler(line: str) -> None，收到每一行終端機輸入時都會呼叫
    """
    _handlers.append(handler)


async def start(bot: commands.Bot) -> None:
    """
    啟動終端機指令監聽背景任務，重複呼叫只會啟動一次。

    Args:
        bot: 機器人實例，用於等待其準備完成
    """
    global _console_task
    if _console_task is not None:
        return
    _console_task = asyncio.create_task(_console_loop(bot))


def stop() -> None:
    """
    停止終端機指令監聽背景任務。
    """
    global _console_task
    if _console_task:
        _console_task.cancel()
        _console_task = None


async def _console_loop(bot: commands.Bot) -> None:
    """
    持續從標準輸入讀取一行指令，依序交給所有已註冊的處理函式。

    Args:
        bot: 機器人實例
    """
    loop = asyncio.get_running_loop()
    await bot.wait_until_ready()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            await asyncio.sleep(1)
            continue
        stripped_line = line.strip()
        for handler in _handlers:
            try:
                await handler(stripped_line)
            except Exception as error:
                logger.error(f"終端機指令處理失敗：{error}", exc_info=True)
