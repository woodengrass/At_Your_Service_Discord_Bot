"""
端到端整合測試：模擬的 Discord on_message 事件 → core/dispatcher.py →
sandbox/worker.py（真的 spawn 子行程）→ Lua 沙箱 → 動作佇列 → 宿主端驗證 →
真正呼叫（假的）discord.py API → plugin_execution_log 稽核紀錄。

刻意不 mock sandbox/worker.py 或 execute_plugin_event()：Track G 的整個重點是
「沙箱執行是真的獨立子行程」，這裡就是要驗證這條真實邊界能正常運作，
見 design.md 第 11 節 Track G 與第 12.5 節測試缺口。這類測試每個都會真的
spawn 一個 Python 子行程，比其餘單元測試慢很多，是預期中的取捨。
"""

import json

from core import bot_registry, database, dispatcher
from tests.integration._fakes import FakeBot, FakeChannel, FakeGuild, Recorder, get_execution_log_rows, install_plugin

GUILD_ID = 900001


async def _setup_db(tmp_path, monkeypatch, name: str):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / name))
    await database.init_db()


async def test_happy_path_sends_message_and_logs_success(tmp_path, monkeypatch):
    await _setup_db(tmp_path, monkeypatch, "happy_path.db")

    recorder = Recorder()
    channel = FakeChannel(recorder, 42)
    guild = FakeGuild(GUILD_ID, recorder, channels={42: channel})
    bot_registry.set_bot(FakeBot(guild))

    plugin_id = "echo_plugin"
    await install_plugin(
        GUILD_ID,
        plugin_id,
        source_code="""
        function on_message(payload)
            api.send_message(payload.channel_id, "echo: " .. payload.content)
        end
        """,
        event_hooks=["on_message"],
        granted_capabilities=["send_message"],
    )

    result = await dispatcher.dispatch_event(GUILD_ID, "on_message", {"channel_id": 42, "content": "hi"})

    assert result is True
    assert recorder.calls == [("send", 42, {"content": "echo: hi", "embed": None, "view": None})]

    logs = await get_execution_log_rows(GUILD_ID, plugin_id)
    assert len(logs) == 1
    assert logs[0]["outcome"] == "success"
    assert logs[0]["error"] is None
    actions = json.loads(logs[0]["actions_json"])
    assert actions == [
        {"type": "send_message", "params": {"channel_id": 42, "content": "echo: hi", "embed": None, "buttons": None}}
    ]

    await database.close_db()


async def test_crashed_plugin_logs_crashed_and_sends_nothing(tmp_path, monkeypatch):
    await _setup_db(tmp_path, monkeypatch, "crashed.db")

    recorder = Recorder()
    channel = FakeChannel(recorder, 42)
    guild = FakeGuild(GUILD_ID, recorder, channels={42: channel})
    bot_registry.set_bot(FakeBot(guild))

    plugin_id = "crashing_plugin"
    await install_plugin(
        GUILD_ID,
        plugin_id,
        # 先排一個延後動作，再故意呼叫未定義的全域函式製造 Lua 執行期錯誤，
        # 驗證「整批回退」：即使前面已經排了動作，崩潰後這些動作也完全不會回傳、不會執行。
        source_code="""
        function on_message(payload)
            api.send_message(payload.channel_id, "before crash")
            this_function_does_not_exist()
        end
        """,
        event_hooks=["on_message"],
        granted_capabilities=["send_message"],
    )

    result = await dispatcher.dispatch_event(GUILD_ID, "on_message", {"channel_id": 42, "content": "hi"})

    assert result is False
    assert recorder.calls == []

    logs = await get_execution_log_rows(GUILD_ID, plugin_id)
    assert len(logs) == 1
    assert logs[0]["outcome"] == "crashed"
    assert logs[0]["error"] is not None

    await database.close_db()


async def test_one_installation_crashes_other_still_succeeds(tmp_path, monkeypatch):
    """
    同一次 dispatch 底下兩個安裝，一個崩潰、一個正常，驗證迴圈會繼續處理下一個
    安裝，不會被前一個安裝的例外中止。跟既有單元測試
    tests/core/test_dispatcher.py::test_dispatch_event_recovers_when_execute_actions_raises
    驗證的是同一件事，但這裡是端到端真的透過 spawn 出來的子行程觸發崩潰，
    不是用 monkeypatch 模擬 _execute_actions() 拋例外。
    """
    await _setup_db(tmp_path, monkeypatch, "mixed_outcomes.db")

    recorder = Recorder()
    channel = FakeChannel(recorder, 42)
    guild = FakeGuild(GUILD_ID, recorder, channels={42: channel})
    bot_registry.set_bot(FakeBot(guild))

    crashing_plugin_id = "crashing_plugin_b"
    healthy_plugin_id = "healthy_plugin_b"

    await install_plugin(
        GUILD_ID,
        crashing_plugin_id,
        source_code="function on_message(payload) this_function_does_not_exist() end",
        event_hooks=["on_message"],
        granted_capabilities=["send_message"],
    )
    await install_plugin(
        GUILD_ID,
        healthy_plugin_id,
        source_code="""
        function on_message(payload)
            api.send_message(payload.channel_id, "still works")
        end
        """,
        event_hooks=["on_message"],
        granted_capabilities=["send_message"],
    )

    result = await dispatcher.dispatch_event(GUILD_ID, "on_message", {"channel_id": 42, "content": "hi"})

    assert result is True
    assert recorder.calls == [("send", 42, {"content": "still works", "embed": None, "view": None})]

    crashing_logs = await get_execution_log_rows(GUILD_ID, crashing_plugin_id)
    healthy_logs = await get_execution_log_rows(GUILD_ID, healthy_plugin_id)
    assert crashing_logs[0]["outcome"] == "crashed"
    assert healthy_logs[0]["outcome"] == "success"

    await database.close_db()
