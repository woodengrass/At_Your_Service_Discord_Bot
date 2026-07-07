"""
純沙箱機制：建立乾淨、有資源限制的空 Lua VM，完全不認識 Discord 或能力 API 是什麼。
第一階段開發重點，見 discord_plugin_platform/design.md 第 5.3、5.4 節。
"""

INSTRUCTION_LIMIT = 500_000
MEMORY_LIMIT_BYTES = 16 * 1024 * 1024
SANDBOX_GLOBAL_ALLOWLIST = {"pairs", "ipairs", "type", "tostring", "tonumber", "table", "string", "math"}


class SandboxExecutionError(Exception):
    """
    沙箱執行失敗時拋出（逾時、超過資源限制、Lua 執行期例外等）。
    """


def create_sandbox_runtime():
    """
    建立一個乾淨的 Lua VM，移除所有危險全域函式，套用執行步數與記憶體限制。

    Returns:
        設定好白名單全域環境與資源限制的 LuaRuntime

    Raises:
        NotImplementedError: 待第一階段實作，需要先確認 lupa 的 register_eval／register_builtins
            關閉方式（見 design.md 第 5.3 節第 3 點），並寫攻擊測試驗證沒有橋接洩漏
    """
    raise NotImplementedError("第一階段實作項目，動工前請先讀 design.md 第 5.3、5.4 節")


def run_with_limits(runtime, lua_function_name: str, payload: dict) -> list[dict]:
    """
    在資源限制下執行沙箱內指定的 Lua 函式。

    Args:
        runtime: create_sandbox_runtime() 建立的 LuaRuntime
        lua_function_name: 要呼叫的外掛事件處理函式名稱
        payload: 事件資料

    Returns:
        外掛執行期間排入佇列的動作清單

    Raises:
        SandboxExecutionError: 執行逾時、超過資源限制，或執行中途拋出未捕捉例外
            （中途崩潰時整批動作清單回退，不回傳任何部分結果，見 design.md 第 3.2 節）
        NotImplementedError: 待第一階段實作
    """
    raise NotImplementedError("第一階段實作項目")
