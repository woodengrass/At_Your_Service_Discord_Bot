"""
run_with_limits() 的基本行為測試：呼叫外掛事件處理函式、收集動作佇列、錯誤傳遞。
"""

import pytest

from sandbox.engine import SandboxExecutionError, create_sandbox_runtime, execute_untrusted_code, run_with_limits


def test_actions_are_collected_and_nested_tables_become_python_dicts():
    """
    確認 action_queue 裡巢狀的 Lua table（例如 params）會被遞迴轉成純 Python dict，
    不會殘留 Lua table 物件給呼叫端（呼叫端可能要做 JSON 序列化或稽核紀錄寫入）。
    """
    runtime = create_sandbox_runtime()
    execute_untrusted_code(
        runtime,
        """
        _action_queue = {}
        function on_message(payload)
            table.insert(_action_queue, {
                type = "send_message",
                params = {content = payload.content, tags = {"a", "b"}},
            })
        end
        """,
    )
    actions = run_with_limits(runtime, "on_message", {"content": "hello"})
    assert actions == [
        {"type": "send_message", "params": {"content": "hello", "tags": ["a", "b"]}}
    ]


def test_nested_payload_is_a_real_lua_table_not_opaque_object():
    """
    payload 裡的巢狀 list/dict（例如 on_slash_command 的 options 陣列）必須轉成真正的
    Lua table，不能是不透明的 POBJECT，否則外掛用 ipairs()/# 走訪就會直接壞掉
    （實測套用範例外掛 plugins_examples/temp_role_punishment 時才抓到：沒有
    recursive=True 時，ipairs(payload.options) 會丟出 IndexError 而不是正常停止）。
    """
    runtime = create_sandbox_runtime()
    execute_untrusted_code(
        runtime,
        """
        function on_slash_command(payload)
            local names = {}
            for _, option in ipairs(payload.options) do
                table.insert(names, option.name)
            end
            _action_queue = {{type = "result", params = {count = #payload.options, names = names}}}
        end
        """,
    )
    actions = run_with_limits(
        runtime,
        "on_slash_command",
        {"options": [{"name": "user_id", "value": "1"}, {"name": "duration", "value": "60"}]},
    )
    assert actions == [
        {"type": "result", "params": {"count": 2, "names": ["user_id", "duration"]}}
    ]


def test_missing_event_handler_raises():
    """
    外掛沒有定義對應的事件處理函式時，應該拋出明確的錯誤而不是讓呼叫端拿到 TypeError。
    """
    runtime = create_sandbox_runtime()
    execute_untrusted_code(runtime, "function on_message(payload) end")
    with pytest.raises(SandboxExecutionError, match="沒有定義事件處理函式"):
        run_with_limits(runtime, "on_scheduled_task", {})


def test_no_action_queue_returns_empty_list():
    """
    外掛沒有建立 _action_queue（例如只讀取資料、不觸發任何動作）時，回傳空清單。
    """
    runtime = create_sandbox_runtime()
    execute_untrusted_code(runtime, "function on_message(payload) end")
    assert run_with_limits(runtime, "on_message", {"content": "x"}) == []


def test_plugin_runtime_error_propagates():
    """
    外掛事件處理函式本身丟出例外時，應該包裝成 SandboxExecutionError 往外拋。
    """
    runtime = create_sandbox_runtime()
    execute_untrusted_code(runtime, "function on_message(payload) error('plugin bug') end")
    with pytest.raises(SandboxExecutionError, match="plugin bug"):
        run_with_limits(runtime, "on_message", {"content": "x"})
