import json

from core import dispatcher
from core.dispatcher import _installation_handles_event, _validate_actions


def test_installation_handles_only_manifest_events() -> None:
    """
    dispatcher 應只把事件分派給 manifest 有訂閱該事件的安裝。
    """
    installation = {
        "plugin_id": "message_logger",
        "manifest_json": json.dumps({"event_hooks": ["on_message_edit"]}),
    }

    assert _installation_handles_event(installation, "on_message_edit") is True
    assert _installation_handles_event(installation, "on_voice_state_update") is False


def test_validate_actions_uses_granted_capabilities_json() -> None:
    """
    動作驗證應解析 granted_capabilities_json，而不是把 JSON 字串拆成字元。
    """
    installation = {"granted_capabilities_json": json.dumps(["send_message"])}

    assert _validate_actions(installation, [{"type": "send_message", "params": {}}]) is True
    assert _validate_actions(installation, [{"type": "add_role", "params": {}}]) is False


async def test_dispatch_event_passes_source_code_and_granted_capabilities(monkeypatch) -> None:
    """
    dispatch_event 應從 repository 讀取原始碼與授權能力後再執行外掛。
    """
    captured_execution: dict = {}
    logged_entries: list[dict] = []

    async def fake_get_enabled_installations_for_guild(guild_id: int) -> list[dict]:
        return [
            {
                "guild_id": guild_id,
                "plugin_id": "message_logger",
                "installed_version": "1.0.0",
                "granted_capabilities_json": json.dumps(["send_message"]),
                "execution_quota_override": None,
                "action_quota_override": None,
                "manifest_json": json.dumps({"event_hooks": ["on_message"]}),
            }
        ]

    async def fake_get_plugin_source(plugin_id: str, version: str) -> str | None:
        assert plugin_id == "message_logger"
        assert version == "1.0.0"
        return "function on_message(payload) end"

    async def fake_execute_plugin_event(**kwargs: object) -> list[dict]:
        captured_execution.update(kwargs)
        return []

    async def fake_check_and_consume_execution_quota(guild_id: int, plugin_id: str) -> bool:
        return True

    async def fake_check_and_consume_action_quota(guild_id: int, plugin_id: str, action_count: int) -> bool:
        return True

    async def fake_execute_actions(guild_id: int, actions: list[dict]) -> None:
        return None

    async def fake_log_execution(
        guild_id: int,
        plugin_id: str,
        event_type: str,
        actions_json: str,
        execution_ms: int,
        outcome: str,
        error: str | None = None,
    ) -> None:
        logged_entries.append({"outcome": outcome, "actions_json": actions_json})

    monkeypatch.setattr(
        dispatcher.repository,
        "get_enabled_installations_for_guild",
        fake_get_enabled_installations_for_guild,
    )
    monkeypatch.setattr(dispatcher.repository, "get_plugin_source", fake_get_plugin_source)
    monkeypatch.setattr(dispatcher.repository, "log_execution", fake_log_execution)
    monkeypatch.setattr(dispatcher.quota, "check_and_consume_execution_quota", fake_check_and_consume_execution_quota)
    monkeypatch.setattr(dispatcher.quota, "check_and_consume_action_quota", fake_check_and_consume_action_quota)
    monkeypatch.setattr(dispatcher.suspension, "is_suspended", lambda plugin_id: False)
    monkeypatch.setattr(dispatcher, "execute_plugin_event", fake_execute_plugin_event)
    monkeypatch.setattr(dispatcher, "_execute_actions", fake_execute_actions)

    dispatch_succeeded = await dispatcher.dispatch_event(1111, "on_message", {"content": "hello"})

    assert captured_execution["source_code"] == "function on_message(payload) end"
    assert captured_execution["granted_capabilities"] == {"send_message"}
    assert dispatch_succeeded is True
    assert logged_entries == [{"outcome": "success", "actions_json": "[]"}]


async def test_dispatch_event_logs_crashed_when_source_code_missing(monkeypatch) -> None:
    """
    找不到外掛原始碼時，dispatcher 應記錄 crashed 並跳過執行。
    """
    logged_errors: list[str | None] = []
    execute_called = False

    async def fake_get_enabled_installations_for_guild(guild_id: int) -> list[dict]:
        return [
            {
                "guild_id": guild_id,
                "plugin_id": "message_logger",
                "installed_version": "missing",
                "granted_capabilities_json": json.dumps(["send_message"]),
                "manifest_json": json.dumps({"event_hooks": ["on_message"]}),
            }
        ]

    async def fake_get_plugin_source(plugin_id: str, version: str) -> str | None:
        return None

    async def fake_check_and_consume_execution_quota(guild_id: int, plugin_id: str) -> bool:
        return True

    async def fake_execute_plugin_event(**kwargs: object) -> list[dict]:
        nonlocal execute_called
        execute_called = True
        return []

    async def fake_log_execution(
        guild_id: int,
        plugin_id: str,
        event_type: str,
        actions_json: str,
        execution_ms: int,
        outcome: str,
        error: str | None = None,
    ) -> None:
        assert outcome == "crashed"
        logged_errors.append(error)

    monkeypatch.setattr(
        dispatcher.repository,
        "get_enabled_installations_for_guild",
        fake_get_enabled_installations_for_guild,
    )
    monkeypatch.setattr(dispatcher.repository, "get_plugin_source", fake_get_plugin_source)
    monkeypatch.setattr(dispatcher.repository, "log_execution", fake_log_execution)
    monkeypatch.setattr(dispatcher.quota, "check_and_consume_execution_quota", fake_check_and_consume_execution_quota)
    monkeypatch.setattr(dispatcher.suspension, "is_suspended", lambda plugin_id: False)
    monkeypatch.setattr(dispatcher, "execute_plugin_event", fake_execute_plugin_event)

    await dispatcher.dispatch_event(1111, "on_message", {"content": "hello"})

    assert execute_called is False
    assert logged_errors == ["找不到外掛原始碼"]


async def test_dispatch_event_filters_target_plugin(monkeypatch) -> None:
    """
    target_plugin_id 應只分派給指定外掛，但仍保留事件訂閱等既有檢查。
    """
    executed_plugin_ids: list[str] = []

    async def fake_get_enabled_installations_for_guild(guild_id: int) -> list[dict]:
        return [
            {
                "guild_id": guild_id,
                "plugin_id": "first_plugin",
                "installed_version": "1.0.0",
                "granted_capabilities_json": json.dumps(["send_message"]),
                "manifest_json": json.dumps({"event_hooks": ["on_scheduled_task"]}),
            },
            {
                "guild_id": guild_id,
                "plugin_id": "second_plugin",
                "installed_version": "1.0.0",
                "granted_capabilities_json": json.dumps(["send_message"]),
                "manifest_json": json.dumps({"event_hooks": ["on_scheduled_task"]}),
            },
        ]

    async def fake_get_plugin_source(plugin_id: str, version: str) -> str | None:
        return "function on_scheduled_task(payload) end"

    async def fake_execute_plugin_event(**kwargs: object) -> list[dict]:
        executed_plugin_ids.append(kwargs["plugin_id"])
        return []

    async def fake_check_and_consume_execution_quota(guild_id: int, plugin_id: str) -> bool:
        return True

    async def fake_execute_actions(guild_id: int, actions: list[dict]) -> None:
        return None

    async def fake_log_execution(
        guild_id: int,
        plugin_id: str,
        event_type: str,
        actions_json: str,
        execution_ms: int,
        outcome: str,
        error: str | None = None,
    ) -> None:
        return None

    monkeypatch.setattr(
        dispatcher.repository,
        "get_enabled_installations_for_guild",
        fake_get_enabled_installations_for_guild,
    )
    monkeypatch.setattr(dispatcher.repository, "get_plugin_source", fake_get_plugin_source)
    monkeypatch.setattr(dispatcher.repository, "log_execution", fake_log_execution)
    monkeypatch.setattr(dispatcher.quota, "check_and_consume_execution_quota", fake_check_and_consume_execution_quota)
    monkeypatch.setattr(dispatcher.suspension, "is_suspended", lambda plugin_id: False)
    monkeypatch.setattr(dispatcher, "execute_plugin_event", fake_execute_plugin_event)
    monkeypatch.setattr(dispatcher, "_execute_actions", fake_execute_actions)

    dispatch_succeeded = await dispatcher.dispatch_event(
        1111,
        "on_scheduled_task",
        {"task_name": "restore", "payload": {}},
        target_plugin_id="second_plugin",
    )

    assert dispatch_succeeded is True
    assert executed_plugin_ids == ["second_plugin"]
