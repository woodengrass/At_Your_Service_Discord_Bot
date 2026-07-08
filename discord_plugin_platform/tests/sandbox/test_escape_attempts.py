"""
沙箱逃逸攻擊測試套件，對應 design.md 第 5.3 節列出的已知手法，每一種手法一個測試案例。
這是第一階段的驗收標準之一，不是可有可無的測試。
"""

import pytest

from sandbox.engine import SandboxExecutionError, create_sandbox_runtime, execute_untrusted_code


def test_cannot_reach_global_table_via_debug_library():
    """
    嘗試透過 debug.getupvalue/getmetatable/getfenv 爬回原始環境，確認拿不到 _G。
    debug 函式庫本身應該已經被清空全域表時移除，所以第一步就會失敗。
    """
    runtime = create_sandbox_runtime()
    with pytest.raises(SandboxExecutionError, match="debug"):
        execute_untrusted_code(runtime, "local d = debug.getinfo(1)")


def test_load_and_loadstring_are_disabled():
    """
    嘗試呼叫 load/loadstring 動態載入程式碼，確認被擋下。
    """
    runtime = create_sandbox_runtime()
    with pytest.raises(SandboxExecutionError, match="load"):
        execute_untrusted_code(runtime, "local f = load('return 1')")

    runtime = create_sandbox_runtime()
    with pytest.raises(SandboxExecutionError, match="loadstring"):
        execute_untrusted_code(runtime, "local f = loadstring('return 1')")


def test_lupa_python_bridge_is_disabled():
    """
    嘗試透過 python.eval 呼叫回 Python 層，確認完全不可行
    （LuaRuntime 建立時已經 register_eval=False, register_builtins=False）。
    """
    runtime = create_sandbox_runtime()
    with pytest.raises(SandboxExecutionError, match="python"):
        execute_untrusted_code(runtime, "python.eval('1')")


def test_single_call_memory_bomb_is_capped():
    """
    真正的單一呼叫記憶體炸彈：一個 string.rep 陳述式，後面沒有任何其他程式碼。

    這跟「迴圈裡重複呼叫 string.rep」不一樣，也更危險：debug.sethook 的計數鉤子
    只在 Lua 指令與指令「之間」才有機會被呼叫，C 函式（string.rep）執行期間完全
    不會被打斷。如果外掛只有這一行陳述式，鉤子可能永遠沒有機會在配置完成前後被
    觸發，記憶體上限（collectgarbage 那個鉤子）完全派不上用場——這裡驗證的是
    engine.py 另外針對 string.rep 加的輸出長度上限（_cap_dangerous_string_functions），
    不是靠通用的步數/記憶體鉤子攔下來的。
    """
    runtime = create_sandbox_runtime()
    with pytest.raises(SandboxExecutionError, match="string.rep"):
        execute_untrusted_code(runtime, 'local bomb = string.rep("x", 1024 * 1024 * 1024)')


def test_looped_memory_bomb_is_also_capped():
    """
    迴圈裡重複呼叫 string.rep（不是單一陳述式）也要被攔下來，這種情境下步數/記憶體
    鉤子本身就足以攔截，跟上面單一陳述式的情境是兩種不同的攻擊面，都要測。
    """
    runtime = create_sandbox_runtime()
    with pytest.raises(SandboxExecutionError):
        execute_untrusted_code(
            runtime,
            """
            local chunks = {}
            for i = 1, 10000 do
                chunks[i] = string.rep("x", 1024 * 1024)
            end
            """,
        )


def test_coroutine_hook_bypass_is_disabled():
    """
    第 6 種逃逸手法：debug.sethook 安裝的鉤子預設只作用在主執行緒，
    若外掛能開 coroutine 在裡面跑無窮迴圈就能完全繞過步數限制。
    因此 coroutine 從一開始就不在白名單內，這裡確認拿不到 coroutine 函式庫。
    """
    runtime = create_sandbox_runtime()
    with pytest.raises(SandboxExecutionError, match="coroutine"):
        execute_untrusted_code(runtime, "local co = coroutine.create(function() end)")


def test_pcall_hook_error_swallowing_is_disabled():
    """
    第 7 種逃逸手法：資源限制鉤子是靠呼叫 error() 中止執行，
    若外掛能用 pcall 包住無窮迴圈接住這個 error，就能無限重新進入迴圈、完全繞過步數上限。
    因此 pcall/xpcall 從白名單移除，這裡確認拿不到 pcall/xpcall。
    """
    runtime = create_sandbox_runtime()
    with pytest.raises(SandboxExecutionError, match="pcall"):
        execute_untrusted_code(
            runtime,
            """
            local function forever() while true do end end
            while true do pcall(forever) end
            """,
        )

    runtime = create_sandbox_runtime()
    with pytest.raises(SandboxExecutionError, match="xpcall"):
        execute_untrusted_code(runtime, "xpcall(function() end, function() end)")


def test_os_and_io_libraries_are_disabled():
    """
    確認 os、io、require 這幾個能直接觸及檔案系統/行程的函式庫也不在白名單內。
    """
    runtime = create_sandbox_runtime()
    with pytest.raises(SandboxExecutionError, match="os"):
        execute_untrusted_code(runtime, "os.execute('echo test')")

    runtime = create_sandbox_runtime()
    with pytest.raises(SandboxExecutionError, match="io"):
        execute_untrusted_code(runtime, "io.open('test.txt', 'w')")

    runtime = create_sandbox_runtime()
    with pytest.raises(SandboxExecutionError, match="require"):
        execute_untrusted_code(runtime, "require('os')")


def test_collectgarbage_is_disabled():
    """
    確認外掛程式碼本身拿不到 collectgarbage，避免用 collectgarbage("stop") 關掉 GC
    來繞過記憶體上限檢查（資源限制鉤子內部透過 local upvalue 保留自己的參照，不受影響）。
    """
    runtime = create_sandbox_runtime()
    with pytest.raises(SandboxExecutionError, match="collectgarbage"):
        execute_untrusted_code(runtime, "collectgarbage('stop')")
