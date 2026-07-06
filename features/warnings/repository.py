import json

from core.database import get_db


def _row_to_warning(guild_id: int, channel_id: int | None, role_id: int | None,
                     active: int, schedule_json: str, content_json: str) -> dict:
    """
    將資料庫的一列資料轉換成與舊版 JSON 結構相容的提醒 dict。

    Args:
        guild_id: 伺服器 ID
        channel_id: 發送頻道 ID
        role_id: 標註身分組 ID
        active: 是否啟用（0/1）
        schedule_json: 排程設定的 JSON 字串
        content_json: 提醒內容的 JSON 字串

    Returns:
        與舊版 JSON 結構相容的提醒設定 dict
    """
    return {
        "guild_id": guild_id,
        "channel_id": channel_id,
        "role_id": role_id,
        "active": bool(active),
        "schedule": json.loads(schedule_json),
        "content": json.loads(content_json),
    }


async def get_all_data() -> dict[str, dict]:
    """
    取得所有伺服器的定時提醒資料，結構與舊版 JSON 檔案相容，供記憶體快取載入使用。

    Returns:
        dict，鍵為提醒 ID，值為提醒設定 dict
    """
    db = get_db()
    all_data: dict[str, dict] = {}
    async with db.execute(
        "SELECT warning_id, guild_id, channel_id, role_id, active, schedule_json, content_json FROM warnings"
    ) as cursor:
        async for warning_id, guild_id, channel_id, role_id, active, schedule_json, content_json in cursor:
            all_data[warning_id] = _row_to_warning(guild_id, channel_id, role_id, active, schedule_json, content_json)
    return all_data


async def set_warning(warning_id: str, warning: dict) -> None:
    """
    新增或更新一筆定時提醒。

    Args:
        warning_id: 提醒 ID
        warning: 提醒設定，需包含 guild_id、schedule、content，可包含 channel_id、role_id、active
    """
    db = get_db()
    await db.execute(
        """
        INSERT INTO warnings (warning_id, guild_id, channel_id, role_id, active, schedule_json, content_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (warning_id) DO UPDATE SET
            guild_id = excluded.guild_id,
            channel_id = excluded.channel_id,
            role_id = excluded.role_id,
            active = excluded.active,
            schedule_json = excluded.schedule_json,
            content_json = excluded.content_json
        """,
        (
            warning_id, warning["guild_id"], warning.get("channel_id"), warning.get("role_id"),
            int(warning.get("active", True)),
            json.dumps(warning.get("schedule", {}), ensure_ascii=False),
            json.dumps(warning.get("content", {}), ensure_ascii=False),
        )
    )
    await db.commit()


async def remove_warning(warning_id: str) -> bool:
    """
    移除指定提醒。

    Args:
        warning_id: 提醒 ID

    Returns:
        True 表示成功移除
    """
    db = get_db()
    cursor = await db.execute("DELETE FROM warnings WHERE warning_id = ?", (warning_id,))
    await db.commit()
    return cursor.rowcount > 0


async def toggle_warning(warning_id: str) -> bool | None:
    """
    切換指定提醒的啟用狀態。

    Args:
        warning_id: 提醒 ID

    Returns:
        切換後的啟用狀態；找不到提醒時回傳 None
    """
    db = get_db()
    async with db.execute("SELECT active FROM warnings WHERE warning_id = ?", (warning_id,)) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None

    new_active = not bool(row[0])
    await db.execute(
        "UPDATE warnings SET active = ? WHERE warning_id = ?", (int(new_active), warning_id)
    )
    await db.commit()
    return new_active


class WarningRepositoryCache:
    """維護定時提醒的記憶體讀取快取。"""

    def __init__(self) -> None:
        self.data: dict[str, dict] = {}

    async def load_cache(self) -> None:
        """從資料庫重新載入全部定時提醒。"""
        self.data = await get_all_data()

    async def set_warning(self, warning_id: str, warning: dict) -> None:
        """新增或更新提醒並同步快取。"""
        await set_warning(warning_id, warning)
        self.data[warning_id] = warning

    async def remove_warning(self, warning_id: str) -> bool:
        """移除提醒並同步快取。"""
        removed = await remove_warning(warning_id)
        self.data.pop(warning_id, None)
        return removed

    async def toggle_warning(self, warning_id: str) -> bool | None:
        """切換提醒啟用狀態並同步快取。"""
        new_active = await toggle_warning(warning_id)
        if new_active is not None and warning_id in self.data:
            self.data[warning_id]["active"] = new_active
        return new_active


WarningStore = WarningRepositoryCache()

