"""
capability_bindings.bind_capabilities() 的整合測試：能力授權過濾、同步/延後執行模式、
Lua<->Python 巢狀資料轉換、以及透過 run_coroutine_sync 橋接到真正資料庫的 storage 能力。

用最小的假 discord 物件（不是真的 discord.py Guild/Member）驗證，因為建構真正的
discord.py Guild/Member 需要完整的 gateway 連線狀態，不適合單元測試；
這裡驗證的是 capability_bindings/capability_api 自己的邏輯，不是 discord.py 本身。
"""

import asyncio

import pytest

from core import database
from core.capability_api import ExecutionContext
from sandbox.capability_bindings import bind_capabilities
from sandbox.engine import create_sandbox_runtime, execute_untrusted_code, run_with_limits


class _FakeRole:
    def __init__(self, role_id, name, position=0):
        self.id = role_id
        self.name = name
        self.position = position


class _FakeMember:
    def __init__(self, user_id, roles):
        self.id = user_id
        self.display_name = f"member-{user_id}"
        self.joined_at = None
        self.roles = roles
        self.bot = False


class _FakeGuild:
    def __init__(self, guild_id, members=None, roles=None):
        self.id = guild_id
        self.name = "測試伺服器"
        self.member_count = len(members or {})
        self._members = members or {}
        self._roles = roles or {}

    def get_member(self, user_id):
        return self._members.get(user_id)

    def get_role(self, role_id):
        return self._roles.get(role_id)

    def get_channel(self, channel_id):
        return None


class _FakeBot:
    def __init__(self, guild):
        self._guild = guild

    def get_guild(self, guild_id):
        return self._guild if guild_id == self._guild.id else None


@pytest.fixture
async def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test_plugin_platform.db"))
    await database.init_db()
    yield
    await database.close_db()


def _make_context(granted_capabilities, guild, event_loop, guild_id=1, plugin_id="test_plugin"):
    return ExecutionContext(
        guild_id=guild_id,
        plugin_id=plugin_id,
        granted_capabilities=granted_capabilities,
        bot=_FakeBot(guild),
        event_loop=event_loop,
    )


def test_unauthorized_capability_is_not_bound():
    """
    沒有授權 manage_roles 的安裝，Lua 環境裡完全拿不到 api.add_role，
    而不是拿到一個「呼叫了才報錯」的空殼函式。
    """
    guild = _FakeGuild(1)
    loop = asyncio.new_event_loop()
    try:
        context = _make_context(set(), guild, loop)
        runtime = create_sandbox_runtime()
        bind_capabilities(runtime, context)
        result = runtime.execute("return api.add_role == nil, api.get_member ~= nil")
        assert result == (True, True)
    finally:
        loop.close()


def test_get_member_returns_indexable_role_ids():
    """
    確認 get_member() 回傳的巢狀 role_ids 是真正的 Lua table（不是不透明的 POBJECT），
    外掛可以用 #、ipairs 這些基本 Lua 語法操作。
    """
    member = _FakeMember(42, [_FakeRole(100, "role-a"), _FakeRole(200, "role-b")])
    guild = _FakeGuild(1, members={42: member})
    loop = asyncio.new_event_loop()
    try:
        context = _make_context(set(), guild, loop)
        runtime = create_sandbox_runtime()
        bind_capabilities(runtime, context)
        result = runtime.execute(
            """
            local m = api.get_member(42)
            return m.id, m.nickname, #m.role_ids, m.role_ids[1], m.role_ids[2]
            """
        )
        assert result == (42, "member-42", 2, 100, 200)
    finally:
        loop.close()


def test_get_member_not_found_returns_nil():
    guild = _FakeGuild(1)
    loop = asyncio.new_event_loop()
    try:
        context = _make_context(set(), guild, loop)
        runtime = create_sandbox_runtime()
        bind_capabilities(runtime, context)
        result = runtime.execute("return api.get_member(999) == nil")
        assert result is True
    finally:
        loop.close()


def test_send_message_is_deferred_with_converted_nested_params():
    """
    send_message 是延後類能力：呼叫當下不應該有真實動作發生，只是記進
    context.action_queue；embed 這種巢狀參數也要正確轉成 Python dict，
    不能殘留 Lua table 物件（否則稽核紀錄寫入/JSON 序列化會失敗）。
    """
    guild = _FakeGuild(1)
    loop = asyncio.new_event_loop()
    try:
        context = _make_context({"send_message"}, guild, loop)
        runtime = create_sandbox_runtime()
        bind_capabilities(runtime, context)
        execute_untrusted_code(
            runtime,
            """
            api.send_message(123, "hello", {title = "t", color = 5})
            """,
        )
        assert context.action_queue == [
            {
                "type": "send_message",
                "params": {
                    "channel_id": 123,
                    "content": "hello",
                    "embed": {"title": "t", "color": 5},
                    "buttons": None,
                },
            }
        ]
    finally:
        loop.close()


async def test_storage_roundtrip_through_real_database(temp_db):
    """
    storage_set/storage_get 是同步能力，內部透過 context.run_coroutine_sync()
    橋接到主 event loop 上真正的 aiosqlite 連線；這裡驗證橋接機制本身可用，
    寫入之後能讀回一致的值，且轉成 Lua table 後外掛用一般語法讀得到。
    """
    guild = _FakeGuild(1)
    loop = asyncio.get_running_loop()
    context = _make_context({"storage"}, guild, loop)
    runtime = create_sandbox_runtime()
    bind_capabilities(runtime, context)

    def run_in_thread():
        execute_untrusted_code(
            runtime,
            """
            api.storage_set("score", 42)
            """,
        )
        return runtime.execute("return api.storage_get('score')")

    result = await loop.run_in_executor(None, run_in_thread)
    assert result == 42


async def test_run_with_limits_integration_with_real_capability_bindings(temp_db):
    """
    端對端驗證：一個外掛的 on_message 處理函式同時用到同步能力（storage_get/set）
    跟延後能力（send_message），確認整條路徑（bind_capabilities -> run_with_limits
    -> context.action_queue）串起來是通的。
    """
    guild = _FakeGuild(1)
    loop = asyncio.get_running_loop()
    context = _make_context({"storage", "send_message"}, guild, loop)
    runtime = create_sandbox_runtime()
    bind_capabilities(runtime, context)
    execute_untrusted_code(
        runtime,
        """
        function on_message(payload)
            local count = api.storage_get("count") or 0
            api.storage_set("count", count + 1)
            api.send_message(payload.channel_id, "count=" .. (count + 1))
        end
        """,
    )

    def run_in_thread():
        return run_with_limits(runtime, "on_message", {"channel_id": 999})

    await loop.run_in_executor(None, run_in_thread)

    assert context.action_queue == [
        {"type": "send_message", "params": {"channel_id": 999, "content": "count=1", "embed": None, "buttons": None}}
    ]
