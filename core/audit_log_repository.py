import datetime

from core.database import get_db

REASON_MAX_LENGTH = 200  # 只保留簡短摘要，不長期保留完整訊息內容


async def add_log_entry(guild_id: int, user_id: int, action_type: str, reason: str) -> None:
    """
    新增一筆稽核紀錄。reason 只保留簡短摘要（截斷至 REASON_MAX_LENGTH），
    不應傳入完整的原始訊息內容，以符合「不無限期保留訊息內容」的資料最小化原則。

    Args:
        guild_id: 伺服器 ID
        user_id: 被處置的使用者 ID
        action_type: 處置類型，例如 "honeypot_ban"、"spam_timeout"
        reason: 簡短的處置原因摘要
    """
    db = get_db()
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO moderation_log (guild_id, user_id, action_type, reason, created_at) VALUES (?, ?, ?, ?, ?)",
        (guild_id, user_id, action_type, reason[:REASON_MAX_LENGTH], created_at)
    )
    await db.commit()


async def get_guild_logs(guild_id: int, limit: int = 50) -> list[dict]:
    """
    取得指定伺服器最近的稽核紀錄。

    Args:
        guild_id: 伺服器 ID
        limit: 最多回傳幾筆，預設 50

    Returns:
        list of dict，依時間新到舊排序
    """
    db = get_db()
    async with db.execute(
        "SELECT user_id, action_type, reason, created_at FROM moderation_log "
        "WHERE guild_id = ? ORDER BY log_id DESC LIMIT ?",
        (guild_id, limit)
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        {"user_id": user_id, "action_type": action_type, "reason": reason, "created_at": created_at}
        for user_id, action_type, reason, created_at in rows
    ]


async def delete_guild_logs(guild_id: int) -> int:
    """
    刪除指定伺服器的所有稽核紀錄。機器人被踢出伺服器時應呼叫此函式，
    避免在沒有正當理由的情況下繼續保留該伺服器的資料。

    Args:
        guild_id: 伺服器 ID

    Returns:
        實際刪除的筆數
    """
    db = get_db()
    cursor = await db.execute("DELETE FROM moderation_log WHERE guild_id = ?", (guild_id,))
    await db.commit()
    return cursor.rowcount


async def delete_user_logs(user_id: int) -> int:
    """
    刪除指定使用者在所有伺服器的稽核紀錄（供資料刪除請求使用）。

    Args:
        user_id: 使用者 ID

    Returns:
        實際刪除的筆數
    """
    db = get_db()
    cursor = await db.execute("DELETE FROM moderation_log WHERE user_id = ?", (user_id,))
    await db.commit()
    return cursor.rowcount

