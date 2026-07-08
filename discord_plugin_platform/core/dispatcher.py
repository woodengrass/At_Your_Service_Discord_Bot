import json
import logging
import time

from core import quota, repository, suspension
from core.capability_api import CAPABILITY_OWNERS
from sandbox.worker import execute_plugin_event

logger = logging.getLogger(__name__)

EXECUTION_TIMEOUT_SECONDS = 2  # 暫定值，待第二階段實測後校準，見 design.md 第 5.4 節


async def dispatch_event(guild_id: int, event_type: str, event_payload: dict) -> None:
    """
    把 Discord 事件分派給該伺服器已安裝、有訂閱這個事件的外掛執行。

    流程：停權檢查 → 執行次數配額檢查 → 建立沙箱執行 → 驗證動作清單 →
    動作次數配額檢查 → 真正執行動作 → 記錄稽核紀錄。

    Args:
        guild_id: 伺服器 ID
        event_type: 事件名稱，對應附錄 A 定義的事件
        event_payload: 事件資料
    """
    installations = await repository.get_enabled_installations_for_guild(guild_id)

    for installation in installations:
        plugin_id = installation["plugin_id"]

        if not _installation_handles_event(installation, event_type):
            continue

        if suspension.is_suspended(plugin_id):
            continue  # 已停權，完全不建立沙箱，不消耗任何執行資源

        if not await quota.check_and_consume_execution_quota(guild_id, plugin_id):
            await repository.log_execution(guild_id, plugin_id, event_type, "[]", 0, "quota_exceeded")
            continue

        source_code = await repository.get_plugin_source(plugin_id, installation["installed_version"])
        if source_code is None:
            await repository.log_execution(
                guild_id,
                plugin_id,
                event_type,
                "[]",
                0,
                "crashed",
                "找不到外掛原始碼",
            )
            continue

        granted_capabilities = set(json.loads(installation["granted_capabilities_json"]))
        started_at = time.monotonic()
        try:
            actions = await execute_plugin_event(
                guild_id=guild_id,
                plugin_id=plugin_id,
                source_code=source_code,
                event_type=event_type,
                event_payload=event_payload,
                granted_capabilities=granted_capabilities,
            )
        except Exception as error:
            execution_ms = int((time.monotonic() - started_at) * 1000)
            logger.error(f"外掛執行失敗（plugin_id={plugin_id}）：{error}", exc_info=True)
            await repository.log_execution(
                guild_id, plugin_id, event_type, "[]", execution_ms, "crashed", str(error)
            )
            continue

        execution_ms = int((time.monotonic() - started_at) * 1000)

        if not _validate_actions(installation, actions):
            await repository.log_execution(
                guild_id, plugin_id, event_type, "[]", execution_ms, "rejected_invalid_action"
            )
            continue

        if actions and not await quota.check_and_consume_action_quota(guild_id, plugin_id, len(actions)):
            await repository.log_execution(guild_id, plugin_id, event_type, "[]", execution_ms, "quota_exceeded")
            continue

        await _execute_actions(guild_id, actions)
        await repository.log_execution(
            guild_id, plugin_id, event_type, str(actions), execution_ms, "success"
        )


def _validate_actions(installation: dict, actions: list[dict]) -> bool:
    """
    驗證沙箱回傳的動作清單是否都在這次安裝授權的能力範圍內。

    Args:
        installation: 安裝紀錄
        actions: 沙箱回傳的動作清單

    Returns:
        True 表示全部通過驗證
    """
    granted_capabilities = set(json.loads(installation["granted_capabilities_json"]))
    for action in actions:
        action_type = action.get("type")
        required_capability = CAPABILITY_OWNERS.get(action_type)
        if required_capability is None or required_capability not in granted_capabilities:
            return False
    return True


def _installation_handles_event(installation: dict, event_type: str) -> bool:
    """
    檢查安裝紀錄對應的 manifest 是否訂閱指定事件。

    Args:
        installation: 安裝紀錄，需包含 manifest_json
        event_type: 事件名稱

    Returns:
        True 表示該外掛訂閱此事件
    """
    try:
        manifest_data = json.loads(installation["manifest_json"])
    except (KeyError, json.JSONDecodeError) as error:
        logger.error(f"解析外掛 manifest 失敗：{error}", exc_info=True)
        return False
    return event_type in manifest_data.get("event_hooks", [])


async def _execute_actions(guild_id: int, actions: list[dict]) -> None:
    """
    依序真正執行動作清單裡的每個動作，呼叫真正的 Discord API。

    Args:
        guild_id: 伺服器 ID
        actions: 已通過驗證的動作清單

    Raises:
        NotImplementedError: 待 bot_integration/listeners.py 接上真正的 discord.py bot 實例後實作
    """
    raise NotImplementedError("待 bot_integration 完成後接上真正的 Discord API 呼叫")
