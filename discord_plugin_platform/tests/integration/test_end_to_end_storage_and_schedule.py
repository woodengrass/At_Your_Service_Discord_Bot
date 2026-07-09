"""
端到端整合測試：外掛透過真的沙箱子行程呼叫 storage/schedule_task 能力，
驗證寫入在一次成功 dispatch 之後真的持久化到 SQLite（見 design.md 第 5.4.2 節
「storage/schedule_task 的交易邊界：專用連線」的 commit 路徑）。
"""

import datetime

from core import bot_registry, database, dispatcher, plugin_storage_repository, repository
from tests.integration._fakes import FakeBot, FakeGuild, Recorder, install_plugin

GUILD_ID = 900002


async def _setup_db(tmp_path, monkeypatch, name: str):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / name))
    await database.init_db()


async def test_storage_write_persists_after_successful_dispatch(tmp_path, monkeypatch):
    await _setup_db(tmp_path, monkeypatch, "storage_success.db")

    recorder = Recorder()
    guild = FakeGuild(GUILD_ID, recorder)
    bot_registry.set_bot(FakeBot(guild))

    plugin_id = "counter_plugin"
    await install_plugin(
        GUILD_ID,
        plugin_id,
        source_code="""
        function on_message(payload)
            local current = api.storage_get("count")
            if current == nil then
                current = 0
            end
            api.storage_set("count", current + 1)
        end
        """,
        event_hooks=["on_message"],
        granted_capabilities=["storage"],
    )

    result_first = await dispatcher.dispatch_event(GUILD_ID, "on_message", {})
    result_second = await dispatcher.dispatch_event(GUILD_ID, "on_message", {})

    assert result_first is True
    assert result_second is True
    assert await plugin_storage_repository.storage_get(GUILD_ID, plugin_id, "count") == 2

    await database.close_db()


async def test_schedule_task_persists_after_successful_dispatch(tmp_path, monkeypatch):
    await _setup_db(tmp_path, monkeypatch, "schedule_success.db")

    recorder = Recorder()
    guild = FakeGuild(GUILD_ID, recorder)
    bot_registry.set_bot(FakeBot(guild))

    plugin_id = "reminder_plugin"
    await install_plugin(
        GUILD_ID,
        plugin_id,
        source_code="""
        function on_message(payload)
            api.schedule_task(60, "reminder", {user_id = 42})
        end
        """,
        event_hooks=["on_message"],
        granted_capabilities=["schedule_task"],
    )

    result = await dispatcher.dispatch_event(GUILD_ID, "on_message", {})
    assert result is True

    far_future = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)).isoformat()
    due_tasks = await repository.get_due_scheduled_tasks(far_future)
    matching_tasks = [task for task in due_tasks if task["plugin_id"] == plugin_id and task["guild_id"] == GUILD_ID]

    assert len(matching_tasks) == 1
    assert '"reminder"' in matching_tasks[0]["payload_json"]

    await database.close_db()
