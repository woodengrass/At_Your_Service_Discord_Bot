"""
獨立執行入口，串接 engine.py + capability_bindings.py，執行單次事件分派。
見 design.md 第 3.2 節元件規格與第 6.4 節資料夾結構說明。
"""

import asyncio

from core.bot_registry import get_bot
from core.capability_api import ExecutionContext
from sandbox.capability_bindings import bind_capabilities
from sandbox.engine import create_sandbox_runtime, execute_untrusted_code, run_with_limits


async def execute_plugin_event(
    guild_id: int,
    plugin_id: str,
    source_code: str,
    event_type: str,
    event_payload: dict,
    granted_capabilities: set[str],
) -> list[dict]:
    """
    執行單次外掛事件分派的完整流程：建立 VM → 綁定能力 → 在背景執行緒載入外掛
    原始碼並呼叫對應事件處理函式 → 收集動作佇列 → 回傳。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        source_code: 外掛的 Lua 原始碼
        event_type: 觸發的事件名稱，對應 Lua 裡的同名函式（例如 on_message）
        event_payload: 事件資料
        granted_capabilities: 這次安裝授權的能力旗標集合

    Returns:
        動作清單，格式見 design.md 第 3.2 節「動作清單格式」

    Raises:
        SandboxExecutionError: 外掛原始碼載入失敗、執行逾時、超過資源限制，
            或執行中途拋出未捕捉例外（中途崩潰時整批動作清單回退，
            不回傳任何部分結果，見 design.md 第 3.2 節）

    Note:
        沙箱執行本身是同步、CPU-bound 的呼叫，這裡用 `loop.run_in_executor()`
        把「載入原始碼」跟「呼叫事件處理函式」一起丟到背景執行緒跑，能力函式
        裡需要真正 I/O（storage、schedule_task、read_message_history）的部分
        再透過 `ExecutionContext.run_coroutine_sync()` 跨執行緒橋接回這個
        event loop，見 design.md 第 3.2 節 Track A.3 的說明。這裡沒有另外用
        子行程隔離，第一階段先用執行緒，之後真的需要行程級隔離時只需要改
        這裡怎麼建立/呼叫沙箱，能力 API 的介面不受影響。
    """
    context = ExecutionContext(
        guild_id=guild_id,
        plugin_id=plugin_id,
        granted_capabilities=granted_capabilities,
        bot=get_bot(),
        event_loop=asyncio.get_running_loop(),
    )

    runtime = create_sandbox_runtime()
    bind_capabilities(runtime, context)

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_sandboxed, runtime, source_code, event_type, event_payload)

    return context.action_queue


def _run_sandboxed(runtime, source_code: str, event_type: str, event_payload: dict) -> None:
    """
    在背景執行緒裡依序載入外掛原始碼、呼叫事件處理函式，兩步都在同一個
    受資源限制保護的 LuaRuntime 上執行。

    Args:
        runtime: 已完成能力綁定的 LuaRuntime
        source_code: 外掛的 Lua 原始碼
        event_type: 要呼叫的事件處理函式名稱
        event_payload: 事件資料

    Raises:
        SandboxExecutionError: 原始碼載入失敗，或事件處理函式執行失敗
    """
    execute_untrusted_code(runtime, source_code)
    run_with_limits(runtime, event_type, event_payload)
