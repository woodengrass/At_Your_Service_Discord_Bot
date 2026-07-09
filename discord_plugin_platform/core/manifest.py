import json
import re
from dataclasses import dataclass

VALID_EVENT_HOOKS = {
    "on_message",
    "on_member_join",
    "on_member_leave",
    "on_slash_command",
    "on_interaction",
    "on_scheduled_task",
    "on_message_edit",
    "on_message_delete",
    "on_voice_state_update",
}

VALID_CAPABILITIES = {
    "send_message",
    "schedule_task",
    "storage",
    "delete_message",
    "manage_roles",
    "moderate_members",
    "read_message_history",
    "manage_threads",
}

SUPPORTED_CAPABILITY_API_VERSIONS = {1}
SLASH_COMMAND_NAME_PATTERN = re.compile(r"^[a-z0-9_-]{1,32}$")
MAX_SLASH_COMMAND_DESCRIPTION_LENGTH = 100


@dataclass
class PluginSlashCommand:
    name: str
    description: str


@dataclass
class PluginManifest:
    name: str
    version: str
    description: str
    capability_api_version: int
    event_hooks: list[str]
    required_capabilities: list[str]
    slash_commands: list[PluginSlashCommand]


class ManifestValidationError(Exception):
    """
    Manifest 驗證失敗時拋出，訊息內容說明具體哪一項規則不符合。
    """


def _parse_slash_commands(slash_commands_data: object) -> list[PluginSlashCommand]:
    """
    驗證並解析 slash command 宣告，對齊 Discord API 的名稱與描述限制。

    Args:
        slash_commands_data: manifest 內的 slash_commands 欄位

    Returns:
        解析後的 slash command 清單

    Raises:
        ManifestValidationError: 欄位格式、名稱或描述不符合限制時拋出
    """
    if not isinstance(slash_commands_data, list):
        raise ManifestValidationError("slash_commands 必須是陣列")

    slash_commands = []
    seen_names: set[str] = set()
    for item in slash_commands_data:
        if not isinstance(item, dict):
            raise ManifestValidationError("slash_commands 每一項都必須是物件")
        name = item.get("name")
        description = item.get("description")
        if not isinstance(name, str) or not SLASH_COMMAND_NAME_PATTERN.match(name):
            raise ManifestValidationError("slash command name 必須是 1-32 字元的小寫英數、底線或連字號")
        if name in seen_names:
            raise ManifestValidationError(f"slash command name 重複：{name}")
        if (
            not isinstance(description, str)
            or not description
            or len(description) > MAX_SLASH_COMMAND_DESCRIPTION_LENGTH
        ):
            raise ManifestValidationError(
                f"slash command description 必須是 1 到 {MAX_SLASH_COMMAND_DESCRIPTION_LENGTH} 字元"
            )
        seen_names.add(name)
        slash_commands.append(PluginSlashCommand(name=name, description=description))
    return slash_commands


def parse_manifest(manifest_json: str) -> PluginManifest:
    """
    解析並驗證外掛 manifest JSON 字串。

    Args:
        manifest_json: manifest 的原始 JSON 字串

    Returns:
        解析後的 PluginManifest

    Raises:
        ManifestValidationError: JSON 格式錯誤，或任何一條驗證規則不符合
    """
    try:
        data = json.loads(manifest_json)
    except json.JSONDecodeError as error:
        raise ManifestValidationError(f"manifest 不是合法的 JSON：{error}") from error

    for required_field in ("name", "version", "description"):
        value = data.get(required_field)
        if not isinstance(value, str) or not value:
            raise ManifestValidationError(f"manifest 缺少必要欄位或欄位不是非空字串：{required_field}")

    event_hooks = data.get("event_hooks", [])
    invalid_hooks = set(event_hooks) - VALID_EVENT_HOOKS
    if invalid_hooks:
        raise ManifestValidationError(f"event_hooks 含有未定義的事件名稱：{invalid_hooks}")

    required_capabilities = data.get("required_capabilities", [])
    invalid_capabilities = set(required_capabilities) - VALID_CAPABILITIES
    if invalid_capabilities:
        raise ManifestValidationError(f"required_capabilities 含有未定義的能力旗標：{invalid_capabilities}")

    capability_api_version = data.get("capability_api_version")
    if capability_api_version not in SUPPORTED_CAPABILITY_API_VERSIONS:
        raise ManifestValidationError(f"capability_api_version 不受支援：{capability_api_version}")

    slash_commands_data = data.get("slash_commands", [])
    if "on_slash_command" in event_hooks and not slash_commands_data:
        raise ManifestValidationError("event_hooks 含有 on_slash_command 時，slash_commands 不能為空")

    slash_commands = _parse_slash_commands(slash_commands_data)

    return PluginManifest(
        name=data["name"],
        version=data["version"],
        description=data["description"],
        capability_api_version=capability_api_version,
        event_hooks=event_hooks,
        required_capabilities=required_capabilities,
        slash_commands=slash_commands,
    )
