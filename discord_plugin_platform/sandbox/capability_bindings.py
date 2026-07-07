"""
把授權範圍內的能力函式綁進 Lua VM，是連接沙箱機制（engine.py）跟能力 API 登錄表
（core/capability_api.py）的橋樑，管理同步／延後執行的區分。
第一階段後期／第二階段開發重點，見 design.md 第 3.2 節「執行模式」。
"""

from core.capability_api import ExecutionContext


def bind_capabilities(runtime, context: ExecutionContext) -> None:
    """
    把 context 授權範圍內的能力函式綁進 Lua VM 的全域 `api` 表。

    Args:
        runtime: 已建立好資源限制的 LuaRuntime（來自 sandbox.engine.create_sandbox_runtime）
        context: 這次執行的上下文，決定哪些能力函式可以被綁定

    Raises:
        NotImplementedError: 待第一階段後期實作，需先完成 core/capability_api.py 的
            get_allowed_functions() 實際邏輯
    """
    raise NotImplementedError("待 core/capability_api.py 的能力函式實作完成後才能接上")
