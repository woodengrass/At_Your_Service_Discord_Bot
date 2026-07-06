import json

from core.database import get_db


async def get_guild_triggers(guild_id: int) -> dict[str, dict]:
    """
    取得指定伺服器的所有觸發詞設定。

    Args:
        guild_id: 伺服器 ID

    Returns:
        dict，鍵為觸發詞，值為包含 response 與 wildcard 的設定
    """
    db = get_db()
    async with db.execute(
        "SELECT trigger_word, response_json, wildcard FROM triggers WHERE guild_id = ?",
        (guild_id,)
    ) as cursor:
        rows = await cursor.fetchall()

    return {
        trigger_word: {"response": json.loads(response_json), "wildcard": bool(wildcard)}
        for trigger_word, response_json, wildcard in rows
    }


async def get_all_triggers() -> dict[int, dict[str, dict]]:
    """
    取得所有伺服器的觸發詞設定，供快取重建使用。

    Returns:
        dict，鍵為伺服器 ID，值為該伺服器的觸發詞設定
    """
    db = get_db()
    async with db.execute("SELECT guild_id, trigger_word, response_json, wildcard FROM triggers") as cursor:
        rows = await cursor.fetchall()

    all_triggers: dict[int, dict[str, dict]] = {}
    for guild_id, trigger_word, response_json, wildcard in rows:
        guild_triggers = all_triggers.setdefault(guild_id, {})
        guild_triggers[trigger_word] = {"response": json.loads(response_json), "wildcard": bool(wildcard)}
    return all_triggers


async def add_trigger(guild_id: int, trigger_word: str, response: str | list[str], wildcard: bool) -> None:
    """
    新增或覆蓋指定伺服器的觸發詞設定。

    Args:
        guild_id: 伺服器 ID
        trigger_word: 觸發詞
        response: 回覆內容，可為單一字串或多筆隨機回覆列表
        wildcard: 是否為模糊比對
    """
    db = get_db()
    await db.execute(
        """
        INSERT INTO triggers (guild_id, trigger_word, response_json, wildcard)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (guild_id, trigger_word) DO UPDATE SET
            response_json = excluded.response_json,
            wildcard = excluded.wildcard
        """,
        (guild_id, trigger_word, json.dumps(response, ensure_ascii=False), int(wildcard))
    )
    await db.commit()


async def delete_trigger(guild_id: int, trigger_word: str) -> bool:
    """
    刪除指定伺服器的觸發詞設定。

    Args:
        guild_id: 伺服器 ID
        trigger_word: 觸發詞

    Returns:
        True 表示成功刪除；若原本就不存在則回傳 False
    """
    db = get_db()
    cursor = await db.execute(
        "DELETE FROM triggers WHERE guild_id = ? AND trigger_word = ?",
        (guild_id, trigger_word)
    )
    await db.commit()
    return cursor.rowcount > 0

