"""
資源限制驗證測試，對應 design.md 第 5.4 節的步數/記憶體/逾時上限。
"""

import time

import pytest

from sandbox.engine import SandboxExecutionError, create_sandbox_runtime, execute_untrusted_code


def test_infinite_loop_is_stopped_by_instruction_limit():
    """
    執行一個無窮迴圈的外掛，確認在 INSTRUCTION_LIMIT 步數內被強制中止，
    且是在合理的短時間內中止（不是靠行程層級逾時才擋下來）。
    """
    runtime = create_sandbox_runtime()
    started_at = time.monotonic()
    with pytest.raises(SandboxExecutionError, match="執行步數超過上限"):
        execute_untrusted_code(runtime, "while true do end")
    assert time.monotonic() - started_at < 5


def test_memory_bomb_is_stopped_by_memory_limit():
    """
    執行一個持續配置記憶體的外掛，確認在 MEMORY_LIMIT_BYTES 內被強制中止。
    """
    runtime = create_sandbox_runtime()
    started_at = time.monotonic()
    with pytest.raises(SandboxExecutionError, match="記憶體用量超過上限"):
        execute_untrusted_code(
            runtime,
            """
            local chunks = {}
            local i = 0
            while true do
                i = i + 1
                chunks[i] = string.rep("x", 1024 * 1024)
            end
            """,
        )
    assert time.monotonic() - started_at < 5


def test_legitimate_script_runs_without_false_positive():
    """
    確認正常、資源用量小的外掛程式碼不會被誤擋。
    """
    runtime = create_sandbox_runtime()
    execute_untrusted_code(
        runtime,
        """
        local sum = 0
        for i = 1, 1000 do
            sum = sum + i
        end
        assert(sum == 500500)
        """,
    )


def test_legitimate_error_propagates_correctly():
    """
    確認外掛程式碼自己呼叫 error() 拋出的例外，會正常傳遞出來，
    不會被誤判成資源限制觸發（error/assert 在白名單內，供外掛正常錯誤處理使用）。
    """
    runtime = create_sandbox_runtime()
    with pytest.raises(SandboxExecutionError, match="外掛自訂錯誤"):
        execute_untrusted_code(runtime, "error('外掛自訂錯誤')")
