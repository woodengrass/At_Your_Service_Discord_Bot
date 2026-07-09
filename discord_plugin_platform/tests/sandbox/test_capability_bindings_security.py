"""
針對第 8 種逃逸手法（能力函式的 Python callable 屬性洩漏，見 design.md 第 5.3 節）
的專門回歸測試：確認 Lua 完全拿不到能力函式的任何屬性，只能呼叫它。
"""

import asyncio

import pytest

from core.capability_api import ExecutionContext, InProcessBackend
from sandbox.capability_bindings import bind_capabilities
from sandbox.engine import SandboxExecutionError, create_sandbox_runtime, execute_untrusted_code

DANGEROUS_ATTRIBUTES = [
    "__globals__",
    "__call__",
    "__class__",
    "__init__",
    "__self__",
    "__func__",
    "__code__",
    "__dict__",
    "__closure__",
]


class _FakeGuild:
    id = 1

    def get_member(self, user_id):
        return None

    def get_channel(self, channel_id):
        return None

    def get_role(self, role_id):
        return None


class _FakeBot:
    def get_guild(self, guild_id):
        return _FakeGuild() if guild_id == 1 else None


def _make_context(granted_capabilities=frozenset()):
    backend = InProcessBackend(
        guild_id=1,
        plugin_id="test_plugin",
        bot=_FakeBot(),
        event_loop=asyncio.new_event_loop(),
    )
    return ExecutionContext(
        guild_id=1,
        plugin_id="test_plugin",
        granted_capabilities=set(granted_capabilities),
        backend=backend,
    )


@pytest.mark.parametrize("attribute_name", DANGEROUS_ATTRIBUTES)
def test_synchronous_capability_blocks_attribute_access(attribute_name):
    """
    get_member 是有回傳值的同步能力，確認拿不到任何屬性。
    """
    runtime = create_sandbox_runtime()
    bind_capabilities(runtime, _make_context())
    with pytest.raises(SandboxExecutionError, match="不能存取能力函式的屬性"):
        execute_untrusted_code(runtime, f"local x = api.get_member.{attribute_name}")


@pytest.mark.parametrize("attribute_name", DANGEROUS_ATTRIBUTES)
def test_deferred_capability_blocks_attribute_access(attribute_name):
    """
    send_message 是延後類、呼叫後回傳 None 的能力，確認拿不到任何屬性。
    """
    runtime = create_sandbox_runtime()
    bind_capabilities(runtime, _make_context({"send_message"}))
    with pytest.raises(SandboxExecutionError, match="不能存取能力函式的屬性"):
        execute_untrusted_code(runtime, f"local x = api.send_message.{attribute_name}")


def test_full_escape_chain_via_globals_is_blocked():
    """
    這是實測抓到、真的能拿到 os.getcwd() 的完整逃逸鏈，修好前這段會真的
    印出主機的實際路徑；修好後光是第一步 __globals__ 就會被擋下來。
    """
    runtime = create_sandbox_runtime()
    bind_capabilities(runtime, _make_context())
    with pytest.raises(SandboxExecutionError, match="不能存取能力函式的屬性"):
        execute_untrusted_code(
            runtime,
            """
            local g = api.random.__globals__
            local builtins = g["__builtins__"]
            local import_fn = builtins["__import__"]
            local os_mod = import_fn("os")
            return os_mod.getcwd()
            """,
        )


def test_legitimate_synchronous_call_still_works():
    runtime = create_sandbox_runtime()
    bind_capabilities(runtime, _make_context())
    result = runtime.execute("return api.random(1, 1)")
    assert result == 1


def test_legitimate_deferred_call_still_works():
    context = _make_context({"send_message"})
    runtime = create_sandbox_runtime()
    bind_capabilities(runtime, context)
    execute_untrusted_code(runtime, "api.send_message(42, 'hi')")
    assert context.action_queue == [
        {"type": "send_message", "params": {"channel_id": 42, "content": "hi", "embed": None, "buttons": None}}
    ]
