"""
sandbox/worker.py 的 execute_plugin_event() 端對端測試：完整跑一次
「建立 VM -> 綁定能力 -> 背景執行緒載入外掛原始碼並呼叫事件處理函式 -> 回傳動作佇列」流程。
"""

import pytest

from core import bot_registry
from sandbox.engine import SandboxExecutionError
from sandbox.worker import execute_plugin_event


class _FakeGuild:
    def __init__(self, guild_id):
        self.id = guild_id
        self.name = "測試伺服器"
        self.member_count = 0

    def get_member(self, user_id):
        return None

    def get_channel(self, channel_id):
        return None

    def get_role(self, role_id):
        return None


class _FakeBot:
    def __init__(self, guild):
        self._guild = guild

    def get_guild(self, guild_id):
        return self._guild if guild_id == self._guild.id else None


@pytest.fixture
def fake_bot():
    # 不用自己清理，tests/conftest.py 的 _reset_bot_registry autouse fixture
    # 每個測試結束後都會自動 bot_registry.set_bot(None)。
    bot = _FakeBot(_FakeGuild(1))
    bot_registry.set_bot(bot)
    return bot


async def test_execute_plugin_event_returns_queued_actions(fake_bot):
    actions = await execute_plugin_event(
        guild_id=1,
        plugin_id="test_plugin",
        source_code="""
        function on_message(payload)
            api.send_message(payload.channel_id, "echo: " .. payload.content)
        end
        """,
        event_type="on_message",
        event_payload={"channel_id": 42, "content": "hi"},
        granted_capabilities={"send_message"},
    )
    assert actions == [
        {"type": "send_message", "params": {"channel_id": 42, "content": "echo: hi", "embed": None, "buttons": None}}
    ]


async def test_execute_plugin_event_without_capability_cannot_call_api(fake_bot):
    with pytest.raises(SandboxExecutionError, match="send_message"):
        await execute_plugin_event(
            guild_id=1,
            plugin_id="test_plugin",
            source_code="""
            function on_message(payload)
                api.send_message(payload.channel_id, "hi")
            end
            """,
            event_type="on_message",
            event_payload={"channel_id": 42, "content": "hi"},
            granted_capabilities=set(),
        )


async def test_execute_plugin_event_propagates_infinite_loop_as_sandbox_error(fake_bot):
    with pytest.raises(SandboxExecutionError, match="執行步數超過上限"):
        await execute_plugin_event(
            guild_id=1,
            plugin_id="test_plugin",
            source_code="""
            function on_message(payload)
                while true do end
            end
            """,
            event_type="on_message",
            event_payload={"channel_id": 42, "content": "hi"},
            granted_capabilities=set(),
        )


async def test_execute_plugin_event_propagates_syntax_error(fake_bot):
    with pytest.raises(SandboxExecutionError):
        await execute_plugin_event(
            guild_id=1,
            plugin_id="test_plugin",
            source_code="this is not valid lua (((",
            event_type="on_message",
            event_payload={"channel_id": 42, "content": "hi"},
            granted_capabilities=set(),
        )


async def test_execute_plugin_event_missing_handler_raises():
    bot_registry.set_bot(_FakeBot(_FakeGuild(1)))
    with pytest.raises(SandboxExecutionError, match="沒有定義事件處理函式"):
        await execute_plugin_event(
            guild_id=1,
            plugin_id="test_plugin",
            source_code="function on_message(payload) end",
            event_type="on_member_join",
            event_payload={},
            granted_capabilities=set(),
        )
