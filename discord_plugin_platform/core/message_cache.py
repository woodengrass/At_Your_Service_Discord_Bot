import time
from collections import deque

MAX_MESSAGES_PER_CHANNEL = 500
MESSAGE_TTL_SECONDS = 24 * 60 * 60
MAX_TOTAL_CACHE_ENTRIES = 200_000  # 全域硬上限，對應設計文件第 4 節的安全網

_channel_messages: dict[int, deque] = {}
_channel_guild_map: dict[int, int] = {}
_total_entries = 0


def is_channel_cached(channel_id: int) -> bool:
    """
    檢查指定頻道目前是否有在快取（代表該伺服器裝了訂閱 on_message_edit/on_message_delete 的外掛）。

    Args:
        channel_id: 頻道 ID

    Returns:
        True 表示該頻道正在被快取
    """
    return channel_id in _channel_messages


def cache_message(guild_id: int, channel_id: int, message_id: int, author_id: int, content: str) -> None:
    """
    快取一則訊息，供之後 on_message_edit/on_message_delete 事件比對原始內容用。

    Args:
        guild_id: 伺服器 ID
        channel_id: 頻道 ID
        message_id: 訊息 ID
        author_id: 發送者 ID
        content: 訊息內容
    """
    global _total_entries
    if _total_entries >= MAX_TOTAL_CACHE_ENTRIES:
        return  # 全域硬上限已滿，暫停快取新訊息，等既有項目自然淘汰

    _channel_guild_map[channel_id] = guild_id
    channel_queue = _channel_messages.setdefault(channel_id, deque())
    for cached_message in list(channel_queue):
        if cached_message["message_id"] == message_id:
            channel_queue.remove(cached_message)
            _total_entries -= 1
            break
    channel_queue.append(
        {"message_id": message_id, "author_id": author_id, "content": content, "cached_at": time.time()}
    )
    _total_entries += 1
    _evict_channel(channel_id)


def _evict_channel(channel_id: int) -> None:
    """
    對指定頻道套用雙重淘汰條件：超過數量上限或超過存活時間。

    Args:
        channel_id: 頻道 ID
    """
    global _total_entries
    channel_queue = _channel_messages.get(channel_id)
    if channel_queue is None:
        return
    now = time.time()
    while channel_queue and (
        len(channel_queue) > MAX_MESSAGES_PER_CHANNEL
        or now - channel_queue[0]["cached_at"] > MESSAGE_TTL_SECONDS
    ):
        channel_queue.popleft()
        _total_entries -= 1


def get_cached_message(channel_id: int, message_id: int) -> dict | None:
    """
    查詢快取中的訊息內容。

    Args:
        channel_id: 頻道 ID
        message_id: 訊息 ID

    Returns:
        dict，包含 author_id、content；找不到則回傳 None
    """
    channel_queue = _channel_messages.get(channel_id)
    if channel_queue is None:
        return None
    for entry in channel_queue:
        if entry["message_id"] == message_id:
            return entry
    return None


def purge_guild(guild_id: int) -> None:
    """
    立刻清除指定伺服器的所有快取項目，用於解除安裝或機器人被移出伺服器時的清理。

    Args:
        guild_id: 伺服器 ID
    """
    global _total_entries
    channels_to_remove = [
        channel_id for channel_id, mapped_guild_id in _channel_guild_map.items() if mapped_guild_id == guild_id
    ]
    for channel_id in channels_to_remove:
        channel_queue = _channel_messages.pop(channel_id, None)
        if channel_queue:
            _total_entries -= len(channel_queue)
        _channel_guild_map.pop(channel_id, None)
