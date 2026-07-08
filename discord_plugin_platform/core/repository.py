import datetime
import json
import logging

from core.database import get_db

logger = logging.getLogger(__name__)

MIN_QUOTA_OVERRIDE = 0
MAX_QUOTA_OVERRIDE = 10_000


def _now_iso() -> str:
    """
    取得目前 UTC 時間的 ISO 格式字串。

    Returns:
        ISO 8601 格式的時間字串
    """
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _plugin_from_row(row: tuple[object, ...]) -> dict:
    """
    將 plugins 查詢結果轉成外掛中繼資料 dict。

    Args:
        row: plugins 資料表查詢結果

    Returns:
        dict，包含外掛中繼資料欄位
    """
    return {
        "plugin_id": row[0],
        "author_id": row[1],
        "name": row[2],
        "latest_version": row[3],
        "status": row[4],
    }


async def submit_plugin_version(
    plugin_id: str,
    author_id: int,
    name: str,
    version: str,
    manifest_json: str,
    source_code: str,
    capability_api_version: int,
) -> None:
    """
    提交一個外掛版本，並同步建立或更新外掛中繼資料。

    Args:
        plugin_id: 外掛 ID
        author_id: 作者 Discord 使用者 ID
        name: 外掛名稱
        version: 外掛版本
        manifest_json: manifest 原始 JSON 字串
        source_code: Lua 原始碼
        capability_api_version: 能力 API 版本
    """
    db = get_db()
    try:
        await db.execute(
            """
            INSERT INTO plugins (plugin_id, author_id, name, latest_version, status)
            VALUES (?, ?, ?, ?, 'pending_review')
            ON CONFLICT(plugin_id) DO UPDATE SET
                author_id = excluded.author_id,
                name = excluded.name,
                latest_version = excluded.latest_version,
                status = 'pending_review'
            """,
            (plugin_id, author_id, name, version),
        )
        await db.execute(
            """
            INSERT INTO plugin_versions
                (plugin_id, version, manifest_json, source_code, capability_api_version, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (plugin_id, version, manifest_json, source_code, capability_api_version, _now_iso()),
        )
        await db.commit()
    except Exception as error:
        await db.rollback()
        logger.error(f"提交外掛版本失敗：{error}", exc_info=True)
        raise


async def get_plugin(plugin_id: str) -> dict | None:
    """
    取得單一外掛的中繼資料。

    Args:
        plugin_id: 外掛 ID

    Returns:
        dict，包含外掛中繼資料；找不到則回傳 None
    """
    db = get_db()
    async with db.execute(
        """
        SELECT plugin_id, author_id, name, latest_version, status
        FROM plugins
        WHERE plugin_id = ?
        """,
        (plugin_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return _plugin_from_row(row)


async def get_plugin_source(plugin_id: str, version: str) -> str | None:
    """
    取得指定外掛版本的 Lua 原始碼。

    Args:
        plugin_id: 外掛 ID
        version: 外掛版本

    Returns:
        Lua 原始碼；找不到則回傳 None
    """
    db = get_db()
    async with db.execute(
        """
        SELECT source_code
        FROM plugin_versions
        WHERE plugin_id = ? AND version = ?
        """,
        (plugin_id, version),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return row[0]


async def get_plugin_manifest(plugin_id: str, version: str) -> str | None:
    """
    取得指定外掛版本的 manifest JSON 字串。

    Args:
        plugin_id: 外掛 ID
        version: 外掛版本

    Returns:
        manifest JSON 字串；找不到則回傳 None
    """
    db = get_db()
    async with db.execute(
        """
        SELECT manifest_json
        FROM plugin_versions
        WHERE plugin_id = ? AND version = ?
        """,
        (plugin_id, version),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return row[0]


async def list_plugins(status: str | None = None) -> list[dict]:
    """
    列出外掛中繼資料，可依狀態篩選。

    Args:
        status: 外掛狀態；None 表示列出全部

    Returns:
        list of dict，外掛中繼資料列表
    """
    db = get_db()
    if status is None:
        async with db.execute(
            """
            SELECT plugin_id, author_id, name, latest_version, status
            FROM plugins
            ORDER BY plugin_id
            """
        ) as cursor:
            rows = await cursor.fetchall()
    else:
        async with db.execute(
            """
            SELECT plugin_id, author_id, name, latest_version, status
            FROM plugins
            WHERE status = ?
            ORDER BY plugin_id
            """,
            (status,),
        ) as cursor:
            rows = await cursor.fetchall()
    return [_plugin_from_row(row) for row in rows]


async def _set_plugin_status(plugin_id: str, status: str) -> bool:
    """
    更新外掛狀態。

    Args:
        plugin_id: 外掛 ID
        status: 新狀態

    Returns:
        True 表示成功更新；若外掛不存在則回傳 False
    """
    db = get_db()
    cursor = await db.execute(
        "UPDATE plugins SET status = ? WHERE plugin_id = ?",
        (status, plugin_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def approve_plugin(plugin_id: str) -> bool:
    """
    核准指定外掛，並寫入審核稽核紀錄。

    Args:
        plugin_id: 外掛 ID

    Returns:
        True 表示成功核准；若外掛不存在則回傳 False
    """
    db = get_db()
    try:
        async with db.execute(
            "SELECT latest_version FROM plugins WHERE plugin_id = ?",
            (plugin_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            await db.commit()
            return False
        latest_version = row[0]
        cursor = await db.execute(
            "UPDATE plugins SET status = 'approved' WHERE plugin_id = ?",
            (plugin_id,),
        )
        await db.execute(
            """
            INSERT INTO plugin_review_log (plugin_id, version, reviewer_action, reason, created_at)
            VALUES (?, ?, 'approved', NULL, ?)
            """,
            (plugin_id, latest_version, _now_iso()),
        )
        await db.commit()
        return True
    except Exception as error:
        await db.rollback()
        logger.error(f"核准外掛失敗：{error}", exc_info=True)
        raise


async def reject_plugin(plugin_id: str, reason: str) -> bool:
    """
    退回指定外掛，並寫入審核稽核紀錄與原因。

    Args:
        plugin_id: 外掛 ID
        reason: 退回原因

    Returns:
        True 表示成功退回；若外掛不存在則回傳 False
    """
    db = get_db()
    try:
        async with db.execute(
            "SELECT latest_version FROM plugins WHERE plugin_id = ?",
            (plugin_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            await db.commit()
            return False
        latest_version = row[0]
        cursor = await db.execute(
            "UPDATE plugins SET status = 'rejected' WHERE plugin_id = ?",
            (plugin_id,),
        )
        await db.execute(
            """
            INSERT INTO plugin_review_log (plugin_id, version, reviewer_action, reason, created_at)
            VALUES (?, ?, 'rejected', ?, ?)
            """,
            (plugin_id, latest_version, reason, _now_iso()),
        )
        await db.commit()
        return True
    except Exception as error:
        await db.rollback()
        logger.error(f"退回外掛失敗：{error}", exc_info=True)
        raise


async def suspend_plugin(plugin_id: str) -> bool:
    """
    停權指定外掛。

    Args:
        plugin_id: 外掛 ID

    Returns:
        True 表示成功停權；若外掛不存在則回傳 False
    """
    return await _set_plugin_status(plugin_id, "suspended")


async def unsuspend_plugin(plugin_id: str) -> bool:
    """
    解除指定外掛停權，將狀態改回 approved。

    Args:
        plugin_id: 外掛 ID

    Returns:
        True 表示成功解除停權；若外掛不存在則回傳 False
    """
    return await _set_plugin_status(plugin_id, "approved")


async def create_installation(
    guild_id: int, plugin_id: str, version: str, granted_capabilities: list[str]
) -> None:
    """
    建立或更新指定伺服器的外掛安裝紀錄。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        version: 安裝版本
        granted_capabilities: 使用者同意授權的能力清單
    """
    db = get_db()
    granted_capabilities_json = json.dumps(granted_capabilities, ensure_ascii=False)
    await db.execute(
        """
        INSERT INTO plugin_installations
            (guild_id, plugin_id, installed_version, granted_capabilities_json, enabled, installed_at)
        VALUES (?, ?, ?, ?, 1, ?)
        ON CONFLICT(guild_id, plugin_id) DO UPDATE SET
            installed_version = excluded.installed_version,
            granted_capabilities_json = excluded.granted_capabilities_json,
            enabled = 1,
            installed_at = excluded.installed_at
        """,
        (guild_id, plugin_id, version, granted_capabilities_json, _now_iso()),
    )
    await db.commit()


async def delete_installation(guild_id: int, plugin_id: str) -> bool:
    """
    刪除指定伺服器的外掛安裝紀錄，並清掉這個安裝底下所有尚未執行的排程任務。

    一併清掉排程任務是必要的，不是順手做的清理：`consume_due_scheduled_tasks()`
    每分鐘會重新分派所有到期任務，如果安裝已經刪除但任務還留著，
    `dispatch_event(target_plugin_id=...)` 永遠找不到對應安裝、永遠回傳失敗，
    任務會被永遠保留、永遠重試，變成每分鐘執行一次、永遠不會成功也不會消失的殭屍任務。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID

    Returns:
        True 表示成功刪除；若安裝紀錄不存在則回傳 False
    """
    db = get_db()
    cursor = await db.execute(
        "DELETE FROM plugin_installations WHERE guild_id = ? AND plugin_id = ?",
        (guild_id, plugin_id),
    )
    await db.execute(
        "DELETE FROM plugin_scheduled_tasks WHERE guild_id = ? AND plugin_id = ?",
        (guild_id, plugin_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def get_due_scheduled_tasks(now_iso: str) -> list[dict]:
    """
    取得所有已到期的外掛排程任務。

    Args:
        now_iso: 目前時間的 ISO 8601 字串

    Returns:
        list of dict，已到期任務列表
    """
    db = get_db()
    async with db.execute(
        """
        SELECT plugin_scheduled_tasks.task_id, plugin_scheduled_tasks.guild_id,
               plugin_scheduled_tasks.plugin_id, plugin_scheduled_tasks.run_at,
               plugin_scheduled_tasks.payload_json,
               plugin_scheduled_tasks.recurring_interval_seconds,
               plugin_versions.manifest_json
        FROM plugin_scheduled_tasks
        JOIN plugin_installations
          ON plugin_installations.guild_id = plugin_scheduled_tasks.guild_id
         AND plugin_installations.plugin_id = plugin_scheduled_tasks.plugin_id
         AND plugin_installations.enabled = 1
        JOIN plugins
          ON plugins.plugin_id = plugin_scheduled_tasks.plugin_id
         AND plugins.status = 'approved'
        JOIN plugin_versions
          ON plugin_versions.plugin_id = plugin_installations.plugin_id
         AND plugin_versions.version = plugin_installations.installed_version
        WHERE plugin_scheduled_tasks.run_at <= ?
        ORDER BY plugin_scheduled_tasks.run_at, plugin_scheduled_tasks.task_id
        """,
        (now_iso,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        {
            "task_id": row[0],
            "guild_id": row[1],
            "plugin_id": row[2],
            "run_at": row[3],
            "payload_json": row[4],
            "recurring_interval_seconds": row[5],
            "manifest_json": row[6],
        }
        for row in rows
    ]


async def update_scheduled_task_run_at(task_id: str, run_at: str) -> bool:
    """
    更新週期性排程任務的下一次執行時間。

    Args:
        task_id: 排程任務 ID
        run_at: 下一次執行時間的 ISO 8601 字串

    Returns:
        True 表示成功更新；若任務不存在則回傳 False
    """
    db = get_db()
    cursor = await db.execute(
        "UPDATE plugin_scheduled_tasks SET run_at = ? WHERE task_id = ?",
        (run_at, task_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def delete_scheduled_task(task_id: str) -> None:
    """
    刪除指定的外掛排程任務。

    Args:
        task_id: 排程任務 ID
    """
    db = get_db()
    await db.execute("DELETE FROM plugin_scheduled_tasks WHERE task_id = ?", (task_id,))
    await db.commit()


async def guild_has_event_subscription(guild_id: int, event_types: set[str]) -> bool:
    """
    檢查指定伺服器是否有啟用外掛訂閱任一事件。

    Args:
        guild_id: 伺服器 ID
        event_types: 要檢查的事件名稱集合

    Returns:
        True 表示至少一個啟用安裝訂閱其中一個事件
    """
    db = get_db()
    async with db.execute(
        """
        SELECT plugin_versions.manifest_json
        FROM plugin_installations
        JOIN plugin_versions
          ON plugin_versions.plugin_id = plugin_installations.plugin_id
         AND plugin_versions.version = plugin_installations.installed_version
        JOIN plugins
          ON plugins.plugin_id = plugin_installations.plugin_id
        WHERE plugin_installations.guild_id = ?
          AND plugin_installations.enabled = 1
          AND plugins.status != 'suspended'
        """,
        (guild_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    for row in rows:
        try:
            manifest_data = json.loads(row[0])
        except json.JSONDecodeError as error:
            logger.error(f"讀取外掛 manifest 事件訂閱失敗：{error}", exc_info=True)
            continue
        if event_types.intersection(manifest_data.get("event_hooks", [])):
            return True
    return False


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
        SELECT plugin_installations.guild_id, plugin_installations.plugin_id,
               plugin_installations.installed_version, plugin_installations.granted_capabilities_json,
               plugin_installations.execution_quota_override, plugin_installations.action_quota_override,
               plugin_versions.manifest_json
        FROM plugin_installations
        JOIN plugin_versions
          ON plugin_versions.plugin_id = plugin_installations.plugin_id
         AND plugin_versions.version = plugin_installations.installed_version
        WHERE plugin_installations.guild_id = ? AND plugin_installations.enabled = 1
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
            "manifest_json": row[6],
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
    for quota_value in (execution_quota, action_quota):
        if quota_value is not None and (quota_value < MIN_QUOTA_OVERRIDE or quota_value > MAX_QUOTA_OVERRIDE):
            raise ValueError(f"配額覆蓋值必須介於 {MIN_QUOTA_OVERRIDE} 到 {MAX_QUOTA_OVERRIDE}")

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
