import datetime
import logging

import aiosqlite

from core.database import get_db

logger = logging.getLogger(__name__)

# 舊版寫死在程式碼裡的預設關鍵字，僅在資料表第一次建立、尚無任何資料時用來做初始匯入
DEFAULT_KEYWORDS = [
    "free-nitro", "steam-gift", "discord-gift", "phishing",
    "grabber", "hack", "free-robux", "bit.ly/sus",
    "d1scord", "dlscord", "discrod", "gift.com", "nitro.com"
]


async def get_all_keywords() -> list[str]:
    """
    取得目前所有的可疑關鍵字。

    Returns:
        list of str，所有關鍵字
    """
    db = get_db()
    async with db.execute("SELECT keyword FROM link_keywords") as cursor:
        rows = await cursor.fetchall()
    return [row[0] for row in rows]


async def add_keyword(keyword: str) -> bool:
    """
    新增一個可疑關鍵字。

    Args:
        keyword: 要新增的關鍵字

    Returns:
        True 表示成功新增；若已存在則回傳 False
    """
    db = get_db()
    try:
        await db.execute("INSERT INTO link_keywords (keyword) VALUES (?)", (keyword,))
        await db.commit()
        return True
    except aiosqlite.IntegrityError:
        return False
    except Exception as error:
        logger.error(f"新增可疑關鍵字失敗（keyword={keyword}）：{error}", exc_info=True)
        raise


async def remove_keyword(keyword: str) -> bool:
    """
    移除一個可疑關鍵字。

    Args:
        keyword: 要移除的關鍵字

    Returns:
        True 表示成功移除；若原本就不存在則回傳 False
    """
    db = get_db()
    cursor = await db.execute("DELETE FROM link_keywords WHERE keyword = ?", (keyword,))
    await db.commit()
    return cursor.rowcount > 0


async def seed_default_keywords_if_empty() -> None:
    """
    若資料表目前是空的，匯入預設的可疑關鍵字清單（僅執行一次）。
    """
    db = get_db()
    async with db.execute("SELECT COUNT(*) FROM link_keywords") as cursor:
        row = await cursor.fetchone()
    if row[0] > 0:
        return

    await db.executemany(
        "INSERT OR IGNORE INTO link_keywords (keyword) VALUES (?)",
        [(keyword,) for keyword in DEFAULT_KEYWORDS]
    )
    await db.commit()
    print(f"[資訊] 已匯入 {len(DEFAULT_KEYWORDS)} 筆預設可疑關鍵字到資料庫。")


async def get_all_scam_hashes() -> list[tuple[str, str]]:
    """
    取得目前所有已知詐騙圖片的感知雜湊值與標籤。

    Returns:
        list of (phash, label) tuple
    """
    db = get_db()
    async with db.execute("SELECT phash, label FROM scam_image_hashes") as cursor:
        rows = await cursor.fetchall()
    return [(row[0], row[1]) for row in rows]


async def add_scam_hash(phash: str, label: str) -> bool:
    """
    新增一筆已知詐騙圖片的感知雜湊值。

    Args:
        phash: 圖片的感知雜湊值（十六進位字串）
        label: 用於辨識來源的標籤（例如檔名）

    Returns:
        True 表示成功新增；若雜湊值已存在則回傳 False
    """
    db = get_db()
    try:
        await db.execute(
            "INSERT INTO scam_image_hashes (phash, label, added_at) VALUES (?, ?, ?)",
            (phash, label, datetime.datetime.now(datetime.timezone.utc).isoformat())
        )
        await db.commit()
        return True
    except aiosqlite.IntegrityError:
        return False


async def remove_scam_hash(phash: str) -> bool:
    """
    移除一筆已知詐騙圖片的感知雜湊值。

    Args:
        phash: 圖片的感知雜湊值（十六進位字串）

    Returns:
        True 表示成功移除；若原本就不存在則回傳 False
    """
    db = get_db()
    cursor = await db.execute("DELETE FROM scam_image_hashes WHERE phash = ?", (phash,))
    await db.commit()
    return cursor.rowcount > 0

