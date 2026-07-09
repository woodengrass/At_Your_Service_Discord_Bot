import time

import aiosqlite
import pytest

from core import bot_registry, database, plugin_storage_repository
from sandbox import worker
from sandbox.engine import SandboxExecutionError
from sandbox.worker import execute_plugin_event


def _hanging_child_process_main(*args: object) -> None:
    """
    測試用子行程進入點：模擬沙箱卡死且完全不送 done 訊息。

    Args:
        args: worker 傳給子行程進入點的原始參數，本測試不需要使用
    """
    time.sleep(30)


class _FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class _FakeMember:
    def __init__(self, user_id: int) -> None:
        self.id = user_id
        self.display_name = f"member-{user_id}"
        self.joined_at = None
        self.roles = [_FakeRole(100), _FakeRole(200)]
        self.bot = False


class _FakeGuild:
    id = 1
    name = "測試伺服器"
    member_count = 1

    def get_member(self, user_id: int):
        return _FakeMember(user_id) if user_id == 42 else None

    def get_channel(self, channel_id: int):
        return None

    def get_role(self, role_id: int):
        return None


class _FakeBot:
    def get_guild(self, guild_id: int):
        return _FakeGuild() if guild_id == 1 else None


@pytest.fixture
def fake_bot() -> _FakeBot:
    """
    註冊主行程端假的 bot，子行程不會直接拿到這個物件。
    """
    bot = _FakeBot()
    bot_registry.set_bot(bot)
    return bot


@pytest.fixture
async def temp_db(tmp_path, monkeypatch):
    """
    建立 process isolation 測試專用資料庫。
    """
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test_process_isolation.db"))
    await database.init_db()
    yield
    await database.close_db()


async def test_process_worker_returns_deferred_actions(fake_bot: _FakeBot) -> None:
    """
    真子行程執行外掛後，應把子行程內累積的 action_queue 回傳主行程。
    """
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


async def test_process_worker_uses_rpc_for_member_lookup(fake_bot: _FakeBot) -> None:
    """
    子行程中的同步能力應透過 RPC 由主行程查詢 bot 快取。
    """
    actions = await execute_plugin_event(
        guild_id=1,
        plugin_id="test_plugin",
        source_code="""
        function on_message(payload)
            local member = api.get_member(42)
            api.send_message(payload.channel_id, member.nickname .. ":" .. #member.role_ids)
        end
        """,
        event_type="on_message",
        event_payload={"channel_id": 42},
        granted_capabilities={"send_message"},
    )

    assert actions[0]["params"]["content"] == "member-42:2"


async def test_process_worker_storage_uses_parent_execution_db(
    fake_bot: _FakeBot,
    temp_db,
) -> None:
    """
    storage RPC 必須使用主行程傳入的 execution_db，讓 dispatcher 事後 rollback 可以生效。
    """
    execution_db = await aiosqlite.connect(database.DB_PATH)
    try:
        await execute_plugin_event(
            guild_id=1,
            plugin_id="test_plugin",
            source_code="""
            function on_message(payload)
                api.storage_set("count", 1)
            end
            """,
            event_type="on_message",
            event_payload={},
            granted_capabilities={"storage"},
            execution_db=execution_db,
        )
        await execution_db.rollback()
    finally:
        await execution_db.close()

    assert await plugin_storage_repository.storage_get(1, "test_plugin", "count") is None


async def test_process_worker_timeout_terminates_child(monkeypatch, fake_bot: _FakeBot) -> None:
    """
    主行程等待真子行程結果逾時時，應終止並回收該子行程，而不是只放棄等待。
    """
    captured_processes = []
    original_terminate_process = worker._terminate_process

    async def capture_terminate_process(process) -> None:
        captured_processes.append(process)
        await original_terminate_process(process)

    monkeypatch.setattr(worker, "EXECUTION_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(worker, "_child_process_main", _hanging_child_process_main)
    monkeypatch.setattr(worker, "_terminate_process", capture_terminate_process)

    with pytest.raises(SandboxExecutionError, match="逾時"):
        await execute_plugin_event(
            guild_id=1,
            plugin_id="test_plugin",
            source_code="function on_message(payload) end",
            event_type="on_message",
            event_payload={},
            granted_capabilities=set(),
        )

    assert len(captured_processes) == 1
    assert captured_processes[0].is_alive() is False
    assert captured_processes[0].exitcode is not None
