from pathlib import Path

from core.manifest import parse_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_DIRECTORY = PROJECT_ROOT / "plugins_examples" / "temp_role_punishment"


def test_temp_role_punishment_manifest_is_valid() -> None:
    """
    範例外掛 manifest 應符合平台 manifest 驗證規則。
    """
    manifest_json = (EXAMPLE_DIRECTORY / "manifest.json").read_text(encoding="utf-8")

    manifest = parse_manifest(manifest_json)

    assert manifest.name == "temp_role_punishment"
    assert manifest.event_hooks == ["on_slash_command", "on_scheduled_task"]
    assert manifest.required_capabilities == ["manage_roles", "schedule_task", "send_message"]
    assert manifest.slash_commands[0].name == "temp_role"


def test_temp_role_punishment_lua_has_required_entrypoints() -> None:
    """
    範例外掛 Lua 原始碼應包含 Track F 指定的主要流程入口與能力呼叫。
    """
    source_code = (EXAMPLE_DIRECTORY / "temp_role_punishment.lua").read_text(encoding="utf-8")

    assert "function on_slash_command(payload)" in source_code
    assert "function on_scheduled_task(payload)" in source_code
    assert "api.get_member_role_ids" in source_code
    assert "api.remove_role" in source_code
    assert "api.add_role" in source_code
    assert "api.schedule_task" in source_code
