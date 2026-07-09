import os
import re

import aiosqlite

DB_PATH = "data/plugin_platform.db"

_connection: aiosqlite.Connection | None = None


async def init_db() -> None:
    """
    初始化資料庫連線並建立所需的資料表，平台啟動時只需呼叫一次。
    """
    global _connection
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    _connection = await aiosqlite.connect(DB_PATH)
    await _connection.execute("PRAGMA journal_mode=WAL")

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS plugins (
            plugin_id TEXT PRIMARY KEY,
            author_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            latest_version TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending_review'
        )
        """
    )

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS plugin_versions (
            plugin_id TEXT NOT NULL,
            version TEXT NOT NULL,
            manifest_json TEXT NOT NULL,
            source_code TEXT NOT NULL,
            capability_api_version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (plugin_id, version)
        )
        """
    )

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS plugin_installations (
            guild_id INTEGER NOT NULL,
            plugin_id TEXT NOT NULL,
            installed_version TEXT NOT NULL,
            granted_capabilities_json TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            execution_quota_override INTEGER,
            action_quota_override INTEGER,
            installed_at TEXT NOT NULL,
            PRIMARY KEY (guild_id, plugin_id)
        )
        """
    )

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS plugin_kv_store (
            guild_id INTEGER NOT NULL,
            plugin_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (guild_id, plugin_id, key)
        )
        """
    )

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS plugin_scheduled_tasks (
            task_id TEXT PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            plugin_id TEXT NOT NULL,
            run_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            recurring_interval_seconds INTEGER
        )
        """
    )
    await _connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_run_at ON plugin_scheduled_tasks (run_at)"
    )
    await _connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_run_at_plugin
        ON plugin_scheduled_tasks (run_at, plugin_id)
        """
    )

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS plugin_execution_log (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            plugin_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actions_json TEXT NOT NULL,
            execution_ms INTEGER NOT NULL,
            outcome TEXT NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    await _connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_execution_log_guild_plugin ON plugin_execution_log (guild_id, plugin_id)"
    )

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS plugin_review_log (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            plugin_id TEXT NOT NULL,
            version TEXT,
            reviewer_action TEXT NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    await _add_column_if_missing("plugin_review_log", "version", "TEXT")
    await _connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_plugin_review_log_plugin ON plugin_review_log (plugin_id)"
    )

    await _connection.commit()


_SQL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ALLOWED_COLUMN_TYPES = {"INTEGER", "TEXT", "REAL", "BLOB"}


async def _add_column_if_missing(table: str, column: str, column_type: str) -> None:
    """
    若指定資料表缺少欄位則新增，讓既有開發資料庫能安全升級。

    Args:
        table: 資料表名稱
        column: 欄位名稱
        column_type: 欄位型別

    Raises:
        ValueError: table/column/column_type 不合法時拋出
    """
    if not _SQL_IDENTIFIER_PATTERN.match(table) or not _SQL_IDENTIFIER_PATTERN.match(column):
        raise ValueError(f"不合法的資料表或欄位名稱：table={table}, column={column}")
    if column_type not in _ALLOWED_COLUMN_TYPES:
        raise ValueError(f"不合法的欄位型別：{column_type}")

    async with _connection.execute(f"PRAGMA table_info({table})") as cursor:
        columns = {row[1] async for row in cursor}
    if column not in columns:
        await _connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def get_db() -> aiosqlite.Connection:
    """
    取得目前的資料庫連線，需先呼叫過 init_db()。

    Returns:
        已建立的 aiosqlite 連線

    Raises:
        RuntimeError: 若尚未呼叫 init_db() 初始化連線
    """
    if _connection is None:
        raise RuntimeError("資料庫尚未初始化，請先呼叫 init_db()")
    return _connection


async def close_db() -> None:
    """
    關閉資料庫連線，平台結束時呼叫。
    """
    global _connection
    if _connection is not None:
        await _connection.close()
        _connection = None
