"""
終端機管理指令，比照現有 honeypot-discord-bot 專案的 admin/console.py 模式。
這是 v1 唯一給平台操作者用的控制介面，跟第 3.4 節的公開市集網頁完全分開，
滿足第 3.5 節「管理功能不能跟公開網頁共用同一個應用」的安全要求。
第二、三階段開發重點（外掛安裝管理、配額調整），見 design.md 第 5.4.1 節。
"""

import logging
import shlex

from core import message_cache, repository, suspension
from core.database import get_db
from core.manifest import ManifestValidationError, parse_manifest

logger = logging.getLogger(__name__)

HELP_TEXT = """
[外掛平台管理工具] 可用指令（直接在終端機輸入後按 Enter）：
  admin plugin list                                          列出所有外掛
  admin plugin review approve <plugin_id>                    核准待審核外掛，轉為上架狀態
  admin plugin review reject <plugin_id> <reason>             退回待審核外掛並記錄原因
  admin plugin install <guild_id> <plugin_id>                 安裝外掛到指定伺服器
  admin plugin uninstall <guild_id> <plugin_id>               從指定伺服器移除外掛
  admin plugin suspend <plugin_id>                            停權指定外掛（跨所有安裝）
  admin plugin unsuspend <plugin_id>                          解除指定外掛停權
  admin plugin quota set <guild_id> <plugin_id> execution=<次數> action=<次數>
                                                               調整指定安裝的動態配額
"""

QUOTA_NAMES = {"execution", "action"}
MESSAGE_CACHE_EVENTS = {"on_message_edit", "on_message_delete"}
MIN_QUOTA_OVERRIDE = 0
MAX_EXECUTION_QUOTA_OVERRIDE = 10_000
MAX_ACTION_QUOTA_OVERRIDE = 10_000


def _parse_quota_value(value: str) -> int | None:
    """
    解析配額參數值。

    Args:
        value: 指令中 execution= 或 action= 後面的文字

    Returns:
        int 表示指定配額；None 表示恢復平台預設值
    """
    if value.lower() in {"none", "null", "default"}:
        return None
    parsed_value = int(value)
    if parsed_value < MIN_QUOTA_OVERRIDE:
        raise ValueError("配額不能是負數")
    return parsed_value


def _parse_quota_arguments(arguments: list[str]) -> tuple[int | None, int | None]:
    """
    解析 quota set 指令的 execution/action 參數。

    Args:
        arguments: 指令中剩餘的 key=value 參數

    Returns:
        execution 與 action 配額覆蓋值

    Raises:
        ValueError: 參數格式錯誤或名稱不支援
    """
    quota_values: dict[str, int | None] = {"execution": None, "action": None}
    seen_names: set[str] = set()
    for argument in arguments:
        if "=" not in argument:
            raise ValueError(f"配額參數格式錯誤：{argument}")
        name, value = argument.split("=", 1)
        if name not in QUOTA_NAMES:
            raise ValueError(f"未知的配額名稱：{name}")
        quota_values[name] = _parse_quota_value(value)
        seen_names.add(name)
    if seen_names != QUOTA_NAMES:
        raise ValueError("quota set 必須同時提供 execution= 與 action=")
    if (
        quota_values["execution"] is not None
        and quota_values["execution"] > MAX_EXECUTION_QUOTA_OVERRIDE
    ):
        raise ValueError(f"execution 配額不能超過 {MAX_EXECUTION_QUOTA_OVERRIDE}")
    if quota_values["action"] is not None and quota_values["action"] > MAX_ACTION_QUOTA_OVERRIDE:
        raise ValueError(f"action 配額不能超過 {MAX_ACTION_QUOTA_OVERRIDE}")
    return quota_values["execution"], quota_values["action"]


async def _handle_list_command() -> None:
    """
    列出目前所有外掛中繼資料。
    """
    plugins = await repository.list_plugins()
    if not plugins:
        print("目前沒有任何外掛。")
        return
    for plugin in plugins:
        print(
            f"{plugin['plugin_id']} | {plugin['name']} | "
            f"version={plugin['latest_version']} | status={plugin['status']}"
        )


async def _handle_install_command(guild_id_text: str, plugin_id: str) -> None:
    """
    將已核准外掛安裝到指定伺服器。

    Args:
        guild_id_text: 指令中的伺服器 ID 文字
        plugin_id: 外掛 ID
    """
    guild_id = int(guild_id_text)
    plugin = await repository.get_plugin(plugin_id)
    if plugin is None:
        print(f"找不到外掛：{plugin_id}")
        return
    if plugin["status"] != "approved":
        print(f"外掛尚未核准，不能安裝：{plugin_id}")
        return

    manifest_json = await repository.get_plugin_manifest(plugin_id, plugin["latest_version"])
    if manifest_json is None:
        print(f"找不到外掛 manifest：{plugin_id}@{plugin['latest_version']}")
        return

    manifest = parse_manifest(manifest_json)
    await repository.create_installation(
        guild_id=guild_id,
        plugin_id=plugin_id,
        version=plugin["latest_version"],
        granted_capabilities=manifest.required_capabilities,
    )
    print(f"已安裝外掛 {plugin_id} 到伺服器 {guild_id}。")


async def _handle_review_command(parts: list[str]) -> None:
    """
    處理外掛審核指令。

    Args:
        parts: shlex 解析後的完整指令片段
    """
    if len(parts) < 5:
        print(HELP_TEXT)
        return
    action = parts[3]
    plugin_id = parts[4]
    if action == "approve" and len(parts) == 5:
        updated = await repository.approve_plugin(plugin_id)
        print("已核准外掛。" if updated else f"找不到外掛：{plugin_id}")
        return
    if action == "reject" and len(parts) >= 6:
        reason = " ".join(parts[5:])
        updated = await repository.reject_plugin(plugin_id, reason)
        print("已退回外掛。" if updated else f"找不到外掛：{plugin_id}")
        return
    print(HELP_TEXT)


async def _handle_quota_command(parts: list[str]) -> None:
    """
    處理指定安裝的配額覆蓋指令。

    Args:
        parts: shlex 解析後的完整指令片段
    """
    if len(parts) < 8 or parts[3] != "set":
        print(HELP_TEXT)
        return
    guild_id = int(parts[4])
    plugin_id = parts[5]
    execution_quota, action_quota = _parse_quota_arguments(parts[6:])
    updated = await repository.set_installation_quota_override(
        guild_id=guild_id,
        plugin_id=plugin_id,
        execution_quota=execution_quota,
        action_quota=action_quota,
    )
    print("已更新外掛安裝配額。" if updated else f"找不到安裝紀錄：{guild_id}/{plugin_id}")


async def handle_command(line: str) -> None:
    """
    解析單行終端機指令。

    Args:
        line: 終端機輸入的一整行文字

    """
    try:
        parts = shlex.split(line)
    except ValueError as error:
        print(f"指令解析失敗：{error}")
        return

    if not parts:
        return
    if len(parts) < 2 or parts[0] != "admin" or parts[1] != "plugin":
        return

    try:
        command = parts[2] if len(parts) >= 3 else ""
        if command == "list" and len(parts) == 3:
            await _handle_list_command()
        elif command == "review":
            await _handle_review_command(parts)
        elif command == "install" and len(parts) == 5:
            await _handle_install_command(parts[3], parts[4])
        elif command == "uninstall" and len(parts) == 5:
            guild_id = int(parts[3])
            plugin_id = parts[4]
            deleted = await repository.delete_installation(guild_id, plugin_id)
            if deleted and not await repository.guild_has_event_subscription(guild_id, MESSAGE_CACHE_EVENTS):
                message_cache.purge_guild(guild_id)
            print("已移除外掛安裝。" if deleted else f"找不到安裝紀錄：{guild_id}/{plugin_id}")
        elif command == "suspend" and len(parts) == 4:
            plugin_id = parts[3]
            updated = await repository.suspend_plugin(plugin_id)
            if updated:
                await suspension.refresh_from_database(get_db())
                print("已停權外掛並同步停權快取。")
            else:
                print(f"找不到外掛：{plugin_id}")
        elif command == "unsuspend" and len(parts) == 4:
            plugin_id = parts[3]
            updated = await repository.unsuspend_plugin(plugin_id)
            if updated:
                await suspension.refresh_from_database(get_db())
                print("已解除外掛停權並同步停權快取。")
            else:
                print(f"找不到外掛：{plugin_id}")
        elif command == "quota":
            await _handle_quota_command(parts)
        else:
            print(HELP_TEXT)
    except (ManifestValidationError, ValueError) as error:
        print(f"指令執行失敗：{error}")
    except Exception as error:
        logger.error(f"外掛平台管理指令執行失敗：{error}", exc_info=True)
        print("指令執行時發生未預期錯誤，請查看日誌。")
