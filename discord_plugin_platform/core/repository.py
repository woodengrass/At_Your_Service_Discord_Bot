import datetime
import logging

from core.database import get_db

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """
    取得目前 UTC 時間的 ISO 格式字串。

    Returns:
        ISO 8601 格式的時間字串
    """
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


async def get_installation(guild_id: int, plugin_id: str) -> dict | None:
    """
    取得指定伺服器對某個外掛的安裝紀錄。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID

    Returns:
        dict，包含安裝紀錄欄位；若尚未安裝則回傳 None
    """
    db = get_db()
    async with db.execute(
        """
        SELECT guild_id, plugin_id, installed_version, granted_capabilities_json, enabled,
               execution_quota_override, action_quota_override
        FROM plugin_installations
        WHERE guild_id = ? AND plugin_id = ?
        """,
        (guild_id, plugin_id),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return {
        "guild_id": row[0],
        "plugin_id": row[1],
        "installed_version": row[2],
        "granted_capabilities_json": row[3],
        "enabled": bool(row[4]),
        "execution_quota_override": row[5],
        "action_quota_override": row[6],
    }


async def get_enabled_installations_for_guild(guild_id: int) -> list[dict]:
    """
    取得指定伺服器目前所有已啟用的外掛安裝紀錄。

    Args:
        guild_id: 伺服器 ID

    Returns:
        list of dict，每筆包含安裝紀錄欄位
    """
    db = get_db()
    async with db.execute(
        """
        SELECT guild_id, plugin_id, installed_version, granted_capabilities_json,
               execution_quota_override, action_quota_override
        FROM plugin_installations
        WHERE guild_id = ? AND enabled = 1
        """,
        (guild_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        {
            "guild_id": row[0],
            "plugin_id": row[1],
            "installed_version": row[2],
            "granted_capabilities_json": row[3],
            "execution_quota_override": row[4],
            "action_quota_override": row[5],
        }
        for row in rows
    ]


async def set_installation_quota_override(
    guild_id: int, plugin_id: str, execution_quota: int | None, action_quota: int | None
) -> bool:
    """
    設定指定安裝的動態配額覆蓋值。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        execution_quota: 每分鐘執行次數上限；None 代表恢復使用平台預設值
        action_quota: 每分鐘動作次數上限；None 代表恢復使用平台預設值

    Returns:
        True 表示成功更新；若該安裝不存在則回傳 False
    """
    db = get_db()
    cursor = await db.execute(
        """
        UPDATE plugin_installations
        SET execution_quota_override = ?, action_quota_override = ?
        WHERE guild_id = ? AND plugin_id = ?
        """,
        (execution_quota, action_quota, guild_id, plugin_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def is_plugin_suspended(plugin_id: str) -> bool:
    """
    檢查指定外掛目前是否處於停權狀態。

    Args:
        plugin_id: 外掛 ID

    Returns:
        True 表示已停權
    """
    db = get_db()
    async with db.execute(
        "SELECT status FROM plugins WHERE plugin_id = ?", (plugin_id,)
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return False
    return row[0] == "suspended"


async def log_execution(
    guild_id: int,
    plugin_id: str,
    event_type: str,
    actions_json: str,
    execution_ms: int,
    outcome: str,
    error: str | None = None,
) -> None:
    """
    記錄一筆外掛執行的稽核紀錄。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        event_type: 觸發執行的事件名稱
        actions_json: 本次執行產生的動作清單（JSON 字串）
        execution_ms: 執行耗時（毫秒）
        outcome: 執行結果，success/quota_exceeded/crashed/rejected_invalid_action
        error: 失敗訊息，僅 crashed/rejected_invalid_action 時才有內容
    """
    db = get_db()
    await db.execute(
        """
        INSERT INTO plugin_execution_log
            (guild_id, plugin_id, event_type, actions_json, execution_ms, outcome, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (guild_id, plugin_id, event_type, actions_json, execution_ms, outcome, error, _now_iso()),
    )
    await db.commit()
