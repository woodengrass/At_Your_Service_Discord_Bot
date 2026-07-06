import json

from core.database import get_db


async def get_all_data() -> dict:
    """
    取得所有自訂面板資料，結構與舊版 JSON 檔案相容，供記憶體快取載入使用。

    Returns:
        dict，格式為 {"panels": {message_id 字串: 面板設定 dict}}
    """
    db = get_db()
    panels: dict[str, dict] = {}
    async with db.execute("SELECT message_id, guild_id, channel_id, buttons_json FROM custom_panels") as cursor:
        async for message_id, guild_id, channel_id, buttons_json in cursor:
            panels[str(message_id)] = {
                "guild_id": guild_id,
                "channel_id": channel_id,
                "message_id": message_id,
                "buttons": json.loads(buttons_json)
            }
    return {"panels": panels}


async def set_panel(message_id: int, panel_config: dict) -> None:
    """
    新增或更新一個自訂面板。

    Args:
        message_id: 面板訊息 ID
        panel_config: 面板設定，需包含 guild_id、buttons，可包含 channel_id
    """
    db = get_db()
    await db.execute(
        """
        INSERT INTO custom_panels (message_id, guild_id, channel_id, buttons_json)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (message_id) DO UPDATE SET
            guild_id = excluded.guild_id,
            channel_id = excluded.channel_id,
            buttons_json = excluded.buttons_json
        """,
        (
            message_id, panel_config["guild_id"], panel_config.get("channel_id"),
            json.dumps(panel_config.get("buttons", {}), ensure_ascii=False)
        )
    )
    await db.commit()


async def remove_panel(message_id: int) -> bool:
    """
    依訊息 ID 移除自訂面板。

    Args:
        message_id: 面板訊息 ID

    Returns:
        True 表示已移除資料
    """
    db = get_db()
    cursor = await db.execute("DELETE FROM custom_panels WHERE message_id = ?", (message_id,))
    await db.commit()
    return cursor.rowcount > 0


class CustomPanelRepositoryCache:
    """維護自訂面板的記憶體讀取快取。"""

    def __init__(self) -> None:
        self.data: dict = {}

    async def load_cache(self) -> None:
        """從資料庫重新載入全部自訂面板。"""
        self.data = await get_all_data()

    async def set_panel(self, message_id: int, panel_config: dict) -> None:
        """新增或更新自訂面板並同步快取。"""
        await set_panel(message_id, panel_config)
        self.data.setdefault("panels", {})[str(message_id)] = panel_config

    async def remove_panel(self, message_id: int) -> bool:
        """移除自訂面板並同步快取。"""
        removed = await remove_panel(message_id)
        self.data.get("panels", {}).pop(str(message_id), None)
        return removed


CustomPanelStore = CustomPanelRepositoryCache()

