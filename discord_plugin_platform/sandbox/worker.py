"""
獨立進程入口，串接 engine.py + capability_bindings.py，執行單次事件分派。
見 design.md 第 3.2 節元件規格與第 6.4 節資料夾結構說明。
"""


async def execute_plugin_event(
    guild_id: int,
    plugin_id: str,
    source_code: str,
    event_type: str,
    event_payload: dict,
    granted_capabilities: set[str],
) -> list[dict]:
    """
    執行單次外掛事件分派的完整流程：建立 VM → 綁定能力 → 呼叫外掛對應的事件處理函式
    → 收集動作佇列 → 回傳。

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
        NotImplementedError: 待第一階段沙箱引擎（engine.py）與能力綁定
            （capability_bindings.py）都完成後才能實作
    """
    raise NotImplementedError("待 sandbox/engine.py 與 sandbox/capability_bindings.py 完成後實作")
