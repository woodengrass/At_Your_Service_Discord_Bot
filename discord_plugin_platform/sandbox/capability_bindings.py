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
        function_name: _SafeCapabilityCallable(function, runtime)
        for function_name, function in allowed_functions.items()
    }
    runtime.globals()["api"] = runtime.table_from(wrapped_functions)


class _SafeCapabilityCallable:
    """
    包住一個能力函式，讓 Lua 只能「呼叫」它，完全不能存取它的任何屬性。

    背景（第三方複查才發現的第 8 種逃逸手法，見 design.md 第 5.3 節）：
    lupa 預設讓任何暴露給 Lua 的 Python callable 同時支援呼叫跟屬性存取，
    實測 `api.random.__globals__` 可以直接拿到該函式所在模組的 globals dict，
    再往下 `__globals__["__builtins__"]["__import__"]("os")` 就能拿到真正的
    Python `os` 模組、執行任意系統呼叫——完全繞過 Lua 層的步數/記憶體上限跟
    全域函式清空，因為這條路徑從頭到尾沒有經過 Lua 自己的全域表。
    `register_eval=False`／`register_builtins=False`（engine.py）只關掉 lupa
    主動提供的 `python.eval(...)` 橋接跟 `python` 全域表，完全不影響「已經
    傳進 Lua 的 Python 物件本身的屬性能不能被戳」，這裡才是真正需要擋的地方。

    只覆寫 `__getattr__` 不夠：`__call__`／`__class__`／`__init__` 這些本來就
    定義在 class 上、正常查找就會成功的屬性不會落到 `__getattr__`，必須整個
    覆寫 `__getattribute__` 才能連這些一起擋下來。這不影響外掛正常呼叫這個
    物件——Python 的呼叫協定（`obj(...)`）是透過型別的 `__call__` slot解析，
    不會經過 instance 的 `__getattribute__`，所以呼叫本身完全不受影響。

    也負責原本 _wrap_capability_function() 做的參數/回傳值轉換：呼叫前把
    Lua table 參數遞迴轉成 Python dict/list，呼叫後如果回傳值是 dict/list，
    轉成真正的 Lua table 再回傳——lupa 預設把 Python dict/list 以不透明的
    userdata 物件（POBJECT）形式暴露給 Lua，外掛拿到後連 `#result.role_ids`、
    `ipairs(result.role_ids)` 這種最基本的 Lua 語法都會直接報錯，一定要用
    `runtime.table_from(value, recursive=True)` 轉成真正的 Lua table。
    """

    __slots__ = ("_function", "_runtime")

    def __init__(self, function: Callable, runtime) -> None:
        object.__setattr__(self, "_function", function)
        object.__setattr__(self, "_runtime", runtime)

    def __call__(self, *lua_args):
        function = object.__getattribute__(self, "_function")
        runtime = object.__getattribute__(self, "_runtime")
        python_args = [lua_value_to_python(arg) for arg in lua_args]
        result = function(*python_args)
        if isinstance(result, (dict, list)):
            return runtime.table_from(result, recursive=True)
        return result

    def __getattribute__(self, name: str):
        raise AttributeError(f"外掛不能存取能力函式的屬性：{name}")
