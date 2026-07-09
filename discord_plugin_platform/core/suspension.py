import asyncio
import logging

import aiosqlite

logger = logging.getLogger(__name__)

REFRESH_INTERVAL_SECONDS = 10

_suspended_plugin_ids: set[str] = set()
_refresh_task: asyncio.Task | None = None


def is_suspended(plugin_id: str) -> bool:
    """
    檢查外掛目前是否處於停權狀態，讀取記憶體快取，不查資料庫。

    Args:
        plugin_id: 外掛 ID

    Returns:
        True 表示已停權
    """
    return plugin_id in _suspended_plugin_ids


async def refresh_from_database(db: aiosqlite.Connection) -> None:
    """
    從資料庫重新整份同步目前的停權清單到記憶體。

    Args:
        db: 資料庫連線
    """
    global _suspended_plugin_ids
    async with db.execute("SELECT plugin_id FROM plugins WHERE status = 'suspended'") as cursor:
        rows = await cursor.fetchall()
    _suspended_plugin_ids = {row[0] for row in rows}


async def start_refresh_loop(db: aiosqlite.Connection) -> None:
    """
    啟動背景任務，每隔 REFRESH_INTERVAL_SECONDS 秒重新從資料庫同步停權清單。

    Args:
        db: 資料庫連線
    """
    global _refresh_task
    if _refresh_task is not None:
        return
    _refresh_task = asyncio.create_task(_refresh_loop(db))
    _refresh_task.add_done_callback(lambda task: _handle_refresh_task_done(task, db))


async def _refresh_loop(db: aiosqlite.Connection) -> None:
    """
    定期同步停權清單；一般例外只記錄並繼續下一輪。

    Args:
        db: 資料庫連線
    """
    while True:
        try:
            await refresh_from_database(db)
        except Exception as error:
            logger.error(f"同步停權清單失敗：{error}", exc_info=True)
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)


def _handle_refresh_task_done(task: asyncio.Task, db: aiosqlite.Connection) -> None:
    """
    停權同步背景任務非預期結束時自動重啟，避免快取永久停止更新。

    Args:
        task: 已結束的背景任務
        db: 資料庫連線
    """
    global _refresh_task
    if task.cancelled():
        return
    try:
        task.result()
    except Exception as error:
        logger.error(f"停權同步背景任務已停止，準備重啟：{error}", exc_info=True)
    _refresh_task = asyncio.create_task(_refresh_loop(db))
    _refresh_task.add_done_callback(lambda next_task: _handle_refresh_task_done(next_task, db))


async def stop_refresh_loop() -> None:
    """
    停止背景同步任務，並等待 task 結束，避免關機時留下未回收的取消中任務。
    """
    global _refresh_task
    if _refresh_task is not None:
        task = _refresh_task
        _refresh_task = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return
