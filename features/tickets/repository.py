from core.database import get_db


async def get_all_data() -> dict[str, dict]:
    """
    取得所有伺服器的客服單與開單面板資料，結構與舊版 JSON 檔案相容，供記憶體快取載入使用。

    Returns:
        dict，鍵為伺服器 ID 字串，值為包含 tickets 與 panels 兩個列表的 dict
    """
    db = get_db()
    all_data: dict[str, dict] = {}

    async with db.execute("SELECT guild_id, channel_id, message_id, reason FROM ticket_panels") as cursor:
        async for guild_id, channel_id, message_id, reason in cursor:
            guild_data = all_data.setdefault(str(guild_id), {"tickets": [], "panels": []})
            guild_data["panels"].append({"channel_id": channel_id, "message_id": message_id, "reason": reason})

    async with db.execute(
        "SELECT guild_id, channel_id, message_id, user_id, reason, active FROM tickets"
    ) as cursor:
        async for guild_id, channel_id, message_id, user_id, reason, active in cursor:
            guild_data = all_data.setdefault(str(guild_id), {"tickets": [], "panels": []})
            guild_data["tickets"].append({
                "channel_id": channel_id, "message_id": message_id, "user_id": user_id,
                "reason": reason, "active": bool(active)
            })

    return all_data


async def add_ticket(guild_id: int, ticket: dict) -> None:
    """
    新增一筆客服單紀錄。

    Args:
        guild_id: 伺服器 ID
        ticket: 客服單資料，需包含 channel_id、message_id、user_id、reason、active
    """
    db = get_db()
    await db.execute(
        """
        INSERT INTO tickets (guild_id, channel_id, message_id, user_id, reason, active)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (guild_id, channel_id) DO UPDATE SET
            message_id = excluded.message_id,
            user_id = excluded.user_id,
            reason = excluded.reason,
            active = excluded.active
        """,
        (
            guild_id, ticket["channel_id"], ticket.get("message_id"), ticket["user_id"],
            ticket.get("reason"), int(ticket.get("active", True))
        )
    )
    await db.commit()


async def remove_ticket(guild_id: int, channel_id: int) -> bool:
    """
    依頻道 ID 移除客服單。

    Args:
        guild_id: 伺服器 ID
        channel_id: 客服單頻道 ID

    Returns:
        True 表示已移除資料
    """
    db = get_db()
    cursor = await db.execute(
        "DELETE FROM tickets WHERE guild_id = ? AND channel_id = ?", (guild_id, channel_id)
    )
    await db.commit()
    return cursor.rowcount > 0


async def add_panel(guild_id: int, panel: dict) -> None:
    """
    新增一筆開單面板紀錄。

    Args:
        guild_id: 伺服器 ID
        panel: 面板資料，需包含 channel_id、message_id、reason
    """
    db = get_db()
    await db.execute(
        """
        INSERT INTO ticket_panels (guild_id, channel_id, message_id, reason)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (guild_id, message_id) DO UPDATE SET
            channel_id = excluded.channel_id,
            reason = excluded.reason
        """,
        (guild_id, panel["channel_id"], panel["message_id"], panel.get("reason", ""))
    )
    await db.commit()


async def remove_panel(guild_id: int, message_id: int) -> bool:
    """
    依訊息 ID 移除開單面板。

    Args:
        guild_id: 伺服器 ID
        message_id: 面板訊息 ID

    Returns:
        True 表示已移除資料
    """
    db = get_db()
    cursor = await db.execute(
        "DELETE FROM ticket_panels WHERE guild_id = ? AND message_id = ?", (guild_id, message_id)
    )
    await db.commit()
    return cursor.rowcount > 0


async def remove_guild(guild_id: int) -> bool:
    """
    移除指定伺服器的全部客服單與面板資料。

    Args:
        guild_id: 伺服器 ID

    Returns:
        True 表示有資料被移除
    """
    db = get_db()
    ticket_cursor = await db.execute("DELETE FROM tickets WHERE guild_id = ?", (guild_id,))
    panel_cursor = await db.execute("DELETE FROM ticket_panels WHERE guild_id = ?", (guild_id,))
    await db.commit()
    return ticket_cursor.rowcount > 0 or panel_cursor.rowcount > 0


class TicketRepositoryCache:
    """維護客服單與客服單面板的記憶體讀取快取。"""

    def __init__(self) -> None:
        self.data: dict[str, dict] = {}

    async def load_cache(self) -> None:
        """從資料庫重新載入全部客服單資料。"""
        self.data = await get_all_data()

    async def add_ticket(self, guild_id: int, ticket: dict) -> None:
        """新增客服單並更新快取。"""
        await add_ticket(guild_id, ticket)
        guild_data = self.data.setdefault(str(guild_id), {"tickets": [], "panels": []})
        guild_data["tickets"].append(ticket)

    async def remove_ticket(self, guild_id: int, channel_id: int) -> bool:
        """移除客服單並更新快取。"""
        removed = await remove_ticket(guild_id, channel_id)
        if removed:
            guild_data = self.data.get(str(guild_id), {})
            guild_data["tickets"] = [
                ticket for ticket in guild_data.get("tickets", []) if ticket.get("channel_id") != channel_id
            ]
        return removed

    async def add_panel(self, guild_id: int, panel: dict) -> None:
        """新增客服單面板並更新快取。"""
        await add_panel(guild_id, panel)
        guild_data = self.data.setdefault(str(guild_id), {"tickets": [], "panels": []})
        guild_data["panels"].append(panel)

    async def remove_panel(self, guild_id: int, message_id: int) -> bool:
        """移除客服單面板並更新快取。"""
        removed = await remove_panel(guild_id, message_id)
        if removed:
            guild_data = self.data.get(str(guild_id), {})
            guild_data["panels"] = [
                panel for panel in guild_data.get("panels", []) if panel.get("message_id") != message_id
            ]
        return removed

    async def remove_guild(self, guild_id: int) -> bool:
        """移除伺服器的全部客服單資料並更新快取。"""
        removed = await remove_guild(guild_id)
        self.data.pop(str(guild_id), None)
        return removed


TicketStore = TicketRepositoryCache()

