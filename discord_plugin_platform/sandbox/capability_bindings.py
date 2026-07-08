"""
把授權範圍內的能力函式綁進 Lua VM，是連接沙箱機制（engine.py）跟能力 API 登錄表
（core/capability_api.py）的橋樑，管理同步／延後執行的區分。
第一階段後期／第二階段開發重點，見 design.md 第 3.2 節「執行模式」。
"""

from typing import Callable

from core.capability_api import ExecutionContext, get_allowed_functions
from sandbox.engine import lua_value_to_python


def bind_capabilities(runtime, context: ExecutionContext) -> None:
    """
    把 context 授權範圍內的能力函式綁進 Lua VM 的全域 `api` 表。

    動作佇列走 context.action_queue（Python list），不是 engine.run_with_limits()
    讀取的 Lua 全域 `_action_queue`——那個是給 engine.py 自己獨立測試用、不知道
    能力 API 存在的裸 Lua VM 使用的機制。呼叫端（sandbox/worker.py）執行完
    run_with_limits() 之後，動作清單請直接讀 context.action_queue，不要依賴
    run_with_limits() 的回傳值（那邊沒有能力綁定的情況下一定回傳空清單）。

    Args:
        runtime: 已建立好資源限制的 LuaRuntime（來自 sandbox.engine.create_sandbox_runtime）
        context: 這次執行的上下文，決定哪些能力函式可以被綁定
    """
    allowed_functions = get_allowed_functions(context)
    wrapped_functions = {
        function_name: _wrap_capability_function(runtime, function)
        for function_name, function in allowed_functions.items()
    }
    runtime.globals()["api"] = runtime.table_from(wrapped_functions)


def _wrap_capability_function(runtime, function: Callable) -> Callable:
    """
    包一層轉換：呼叫前把外掛傳進來的 Lua table 參數遞迴轉成 Python dict/list
    （能力函式本身用純 Python 型別寫，不該認識 Lua table 是什麼）；
    呼叫後如果回傳值是 dict/list，轉成真正的 Lua table 再回傳給外掛。

    這一步是必要的，不是防禦性寫法而已：lupa 預設把 Python dict/list
    以不透明的 userdata 物件（POBJECT）形式暴露給 Lua，外掛拿到之後
    連 `#result.role_ids`、`ipairs(result.role_ids)` 這種最基本的 Lua
    語法都會直接報錯（`attempt to get length of a POBJECT value`），
    必須用 `runtime.table_from(value, recursive=True)` 轉成真正的 Lua table
    外掛才能用一般 Lua 語法正常操作巢狀資料。

    Args:
        runtime: 目前的 LuaRuntime，轉換回傳值時需要用它建立 Lua table
        function: 未包裝的能力函式（純 Python 型別的參數與回傳值）

    Returns:
        包裝後、可以直接綁進 Lua 環境呼叫的函式
    """

    def wrapped(*lua_args):
        python_args = [lua_value_to_python(arg) for arg in lua_args]
        result = function(*python_args)
        if isinstance(result, (dict, list)):
            return runtime.table_from(result, recursive=True)
        return result

    return wrapped
