"""
外掛專屬 KV 儲存（`storage_*` 能力）與排程任務（`schedule_task` 能力）的資料存取層。

刻意獨立於 core/repository.py（Track B 負責的外掛市集/審核資料表存取層），避免兩邊
同時改同一個檔案造成合併衝突；這裡管的 plugin_kv_store、plugin_scheduled_tasks
兩張表也跟市集審核邏輯無關，是能力 API 專屬的資料。
"""

import datetime
import json
import time
import uuid
from typing import Any

from core.database import get_db

# storage 能力沒有任何大小/數量上限的話，外掛可以把 SQLite 當成無限儲存空間濫用
# （不管是惡意還是單純寫壞的迴圈），這幾個常數就是防這個的軟性上限，數字不是
# 精算出來的，是「一般排行榜/計數器類外掛用得很夠、濫用起來很快就會撞到」的量級，
# 之後有真實用量數據再校準（比照 design.md 第 5.4 節資源限制的做法）。
MAX_STORAGE_KEY_LENGTH = 256
MAX_STORAGE_VALUE_BYTES = 64 * 1024
MAX_STORAGE_KEYS_PER_INSTALLATION = 1000
MAX_LEADERBOARD_LIMIT = 100
MAX_SCHEDULED_TASKS_PER_INSTALLATION = 1000
MAX_SCHEDULED_TASK_NAME_LENGTH = 128
MAX_SCHEDULED_TASK_PAYLOAD_BYTES = 16 * 1024
MIN_SCHEDULE_DELAY_SECONDS = 1
MAX_SCHEDULE_DELAY_SECONDS = 60 * 60 * 24 * 365
MIN_RECURRING_INTERVAL_SECONDS = 60


class StorageLimitExceededError(Exception):
    """
    storage_set() 超過 key 長度、value 大小或每個安裝的 key 數量上限時拋出。
    """


class ScheduledTaskLimitExceededError(Exception):
    """
    schedule_task() 超過數量、payload 大小或時間範圍限制時拋出。
    """


def _now_iso() -> str:
    """
    取得目前 UTC 時間的 ISO 格式字串。

    Returns:
        ISO 8601 格式的時間字串
    """
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _escape_like_pattern(prefix: str) -> str:
    """
    跳脫 LIKE 語法裡的萬用字元，避免外掛傳入的 prefix 裡剛好含有 % 或 _
    被誤判成萬用字元，導致查詢結果跟外掛預期的不一致。

    Args:
        prefix: 外掛傳入的 key 前綴

    Returns:
        跳脫過的字串，需搭配 `LIKE ... ESCAPE '\\'` 使用
    """
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def storage_get(guild_id: int, plugin_id: str, key: str) -> Any:
    """
    讀取外掛專屬的 KV 資料，以 (guild_id, plugin_id, key) 隔離。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        key: 資料鍵值

    Returns:
        對應的值（已還原成原本的 JSON 型別）；找不到則回傳 None
    """
    db = get_db()
    async with db.execute(
        "SELECT value_json FROM plugin_kv_store WHERE guild_id = ? AND plugin_id = ? AND key = ?",
        (guild_id, plugin_id, key),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return json.loads(row[0])


async def storage_set(guild_id: int, plugin_id: str, key: str, value: Any) -> None:
    """
    寫入外掛專屬的 KV 資料，key 已存在則覆蓋。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        key: 資料鍵值
        value: 要儲存的值，必須是可以 JSON 序列化的型別

    Raises:
        StorageLimitExceededError: key 長度、value 大小超過上限，或這個安裝已經用滿
            MAX_STORAGE_KEYS_PER_INSTALLATION 筆資料且這是一個新 key（覆蓋既有 key 不受此限）
    """
    if len(key) > MAX_STORAGE_KEY_LENGTH:
        raise StorageLimitExceededError(f"key 長度超過上限（{MAX_STORAGE_KEY_LENGTH} 字元）")

    value_json = json.dumps(value)
    if len(value_json.encode("utf-8")) > MAX_STORAGE_VALUE_BYTES:
        raise StorageLimitExceededError(f"value 大小超過上限（{MAX_STORAGE_VALUE_BYTES} bytes）")

    db = get_db()
    async with db.execute(
        "SELECT 1 FROM plugin_kv_store WHERE guild_id = ? AND plugin_id = ? AND key = ?",
        (guild_id, plugin_id, key),
    ) as cursor:
        key_already_exists = await cursor.fetchone() is not None

    if not key_already_exists:
        async with db.execute(
            "SELECT COUNT(*) FROM plugin_kv_store WHERE guild_id = ? AND plugin_id = ?",
            (guild_id, plugin_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row[0] >= MAX_STORAGE_KEYS_PER_INSTALLATION:
            raise StorageLimitExceededError(
                f"這個安裝的 storage key 數量已達上限（{MAX_STORAGE_KEYS_PER_INSTALLATION} 筆）"
            )

    await db.execute(
        """
        INSERT INTO plugin_kv_store (guild_id, plugin_id, key, value_json, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (guild_id, plugin_id, key)
        DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
        """,
        (guild_id, plugin_id, key, value_json, _now_iso()),
    )
    await db.commit()


async def storage_delete(guild_id: int, plugin_id: str, key: str) -> None:
    """
    刪除外掛專屬的 KV 資料，key 不存在時安靜跳過。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        key: 資料鍵值
    """
    db = get_db()
    await db.execute(
        "DELETE FROM plugin_kv_store WHERE guild_id = ? AND plugin_id = ? AND key = ?",
        (guild_id, plugin_id, key),
    )
    await db.commit()


async def storage_list_keys(guild_id: int, plugin_id: str, prefix: str) -> list[str]:
    """
    列舉指定前綴的所有 key。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        prefix: key 前綴，空字串代表列出全部

    Returns:
        符合前綴的 key 清單
    """
    db = get_db()
    async with db.execute(
        "SELECT key FROM plugin_kv_store WHERE guild_id = ? AND plugin_id = ? AND key LIKE ? ESCAPE '\\'",
        (guild_id, plugin_id, _escape_like_pattern(prefix) + "%"),
    ) as cursor:
        rows = await cursor.fetchall()
    return [row[0] for row in rows]


async def storage_get_leaderboard(guild_id: int, plugin_id: str, prefix: str, limit: int) -> list[dict]:
    """
    依數值由大到小排序，回傳指定前綴底下的前 limit 筆資料，由宿主端排序，
    避免外掛在 Lua 裡自己排序耗盡執行步數。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        prefix: key 前綴
        limit: 最多回傳幾筆

    Returns:
        list of {"key": str, "value": int | float}，只包含值為數字的項目，
        非數字的值（例如字串、巢狀物件）會被跳過，不計入排行榜
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > MAX_LEADERBOARD_LIMIT:
        raise StorageLimitExceededError(f"leaderboard limit 必須介於 1 到 {MAX_LEADERBOARD_LIMIT}")

    db = get_db()
    async with db.execute(
        "SELECT key, value_json FROM plugin_kv_store WHERE guild_id = ? AND plugin_id = ? AND key LIKE ? ESCAPE '\\'",
        (guild_id, plugin_id, _escape_like_pattern(prefix) + "%"),
    ) as cursor:
        rows = await cursor.fetchall()

    entries = []
    for key, value_json in rows:
        value = json.loads(value_json)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            entries.append({"key": key, "value": value})

    entries.sort(key=lambda entry: entry["value"], reverse=True)
    return entries[:limit]


async def create_scheduled_task(
    guild_id: int,
    plugin_id: str,
    delay_seconds: float,
    task_name: str,
    payload: dict,
    recurring_interval_seconds: int | None = None,
) -> str:
    """
    建立一筆排程任務，時間到由 Track D 的排程消費迴圈觸發 on_scheduled_task 事件。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        delay_seconds: 幾秒後執行
        task_name: 任務名稱，會原樣傳回 on_scheduled_task 事件的 payload
        payload: 任務資料，會原樣傳回 on_scheduled_task 事件的 payload
        recurring_interval_seconds: 週期性任務的重複間隔秒數，None 代表只執行一次

    Returns:
        新建立的任務 ID，可用於之後呼叫 cancel_scheduled_task 取消
    """
    if (
        not isinstance(delay_seconds, (int, float))
        or isinstance(delay_seconds, bool)
        or delay_seconds < MIN_SCHEDULE_DELAY_SECONDS
        or delay_seconds > MAX_SCHEDULE_DELAY_SECONDS
    ):
        raise ScheduledTaskLimitExceededError(
            f"delay_seconds 必須介於 {MIN_SCHEDULE_DELAY_SECONDS} 到 {MAX_SCHEDULE_DELAY_SECONDS} 秒"
        )
    if not isinstance(task_name, str) or not task_name or len(task_name) > MAX_SCHEDULED_TASK_NAME_LENGTH:
        raise ScheduledTaskLimitExceededError(
            f"task_name 長度必須介於 1 到 {MAX_SCHEDULED_TASK_NAME_LENGTH} 字元"
        )
    if not isinstance(payload, dict):
        raise ScheduledTaskLimitExceededError("payload 必須是 JSON 物件")
    if recurring_interval_seconds is not None and (
        not isinstance(recurring_interval_seconds, int)
        or isinstance(recurring_interval_seconds, bool)
        or recurring_interval_seconds < MIN_RECURRING_INTERVAL_SECONDS
    ):
        raise ScheduledTaskLimitExceededError(
            f"recurring_interval_seconds 必須至少 {MIN_RECURRING_INTERVAL_SECONDS} 秒"
        )

    payload_json = json.dumps({"task_name": task_name, "payload": payload})
    if len(payload_json.encode("utf-8")) > MAX_SCHEDULED_TASK_PAYLOAD_BYTES:
        raise ScheduledTaskLimitExceededError(
            f"payload 大小超過上限（{MAX_SCHEDULED_TASK_PAYLOAD_BYTES} bytes）"
        )

    db = get_db()
    async with db.execute(
        "SELECT COUNT(*) FROM plugin_scheduled_tasks WHERE guild_id = ? AND plugin_id = ?",
        (guild_id, plugin_id),
    ) as cursor:
        row = await cursor.fetchone()
    if row[0] >= MAX_SCHEDULED_TASKS_PER_INSTALLATION:
        raise ScheduledTaskLimitExceededError(
            f"這個安裝的排程任務數量已達上限（{MAX_SCHEDULED_TASKS_PER_INSTALLATION} 筆）"
        )

    task_id = str(uuid.uuid4())
    run_at = datetime.datetime.fromtimestamp(
        time.time() + delay_seconds, tz=datetime.timezone.utc
    ).isoformat()
    await db.execute(
        """
        INSERT INTO plugin_scheduled_tasks
            (task_id, guild_id, plugin_id, run_at, payload_json, recurring_interval_seconds)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            guild_id,
            plugin_id,
            run_at,
            payload_json,
            recurring_interval_seconds,
        ),
    )
    await db.commit()
    return task_id


async def cancel_scheduled_task(guild_id: int, plugin_id: str, task_id: str) -> bool:
    """
    取消一筆尚未執行的排程任務，只能取消自己外掛在自己伺服器安裝底下建立的任務。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        task_id: 要取消的任務 ID

    Returns:
        True 表示確實刪除了一筆；False 表示找不到（可能已經執行過或 ID 錯誤）
    """
    db = get_db()
    cursor = await db.execute(
        "DELETE FROM plugin_scheduled_tasks WHERE task_id = ? AND guild_id = ? AND plugin_id = ?",
        (task_id, guild_id, plugin_id),
    )
    await db.commit()
    return cursor.rowcount > 0
