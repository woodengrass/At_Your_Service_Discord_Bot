import heapq
import time
from collections import OrderedDict

MAX_MESSAGES_PER_CHANNEL = 500
MESSAGE_TTL_SECONDS = 24 * 60 * 60
MAX_TOTAL_CACHE_ENTRIES = 200_000  # 全域硬上限，對應設計文件第 4 節的安全網

_channel_messages: dict[int, OrderedDict[int, dict]] = {}
_channel_guild_map: dict[int, int] = {}
_global_heap: list[tuple[float, int, int]] = []
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
    _channel_guild_map[channel_id] = guild_id
    channel_messages = _channel_messages.setdefault(channel_id, OrderedDict())
    already_cached = message_id in channel_messages
    channel_messages.pop(message_id, None)
    cached_at = time.time()
    channel_messages[message_id] = {
        "message_id": message_id,
        "author_id": author_id,
        "content": content,
        "cached_at": cached_at,
    }
    heapq.heappush(_global_heap, (cached_at, channel_id, message_id))
    if not already_cached:
        _total_entries += 1
    _evict_channel(channel_id)
    _evict_global()


def _evict_channel(channel_id: int) -> None:
    """
    對指定頻道套用雙重淘汰條件：超過數量上限或超過存活時間。

    Args:
        channel_id: 頻道 ID
    """
    global _total_entries
    channel_messages = _channel_messages.get(channel_id)
    if channel_messages is None:
        return
    now = time.time()
    while channel_messages and (
        len(channel_messages) > MAX_MESSAGES_PER_CHANNEL
        or now - next(iter(channel_messages.values()))["cached_at"] > MESSAGE_TTL_SECONDS
    ):
        channel_messages.popitem(last=False)
        _total_entries -= 1
    _remove_channel_if_empty(channel_id)


def _evict_global() -> None:
    """
    套用全域上限，超過時淘汰最舊的快取項目。
    """
    global _total_entries
    while _total_entries > MAX_TOTAL_CACHE_ENTRIES:
        if not _global_heap:
            _total_entries = 0
            return
        cached_at, channel_id, message_id = heapq.heappop(_global_heap)
        channel_messages = _channel_messages.get(channel_id)
        if channel_messages is None:
            continue
        cached_message = channel_messages.get(message_id)
        if cached_message is None or cached_message["cached_at"] != cached_at:
            continue
        channel_messages.pop(message_id)
        _total_entries -= 1
        _remove_channel_if_empty(channel_id)


def _remove_channel_if_empty(channel_id: int) -> None:
    """
    指定頻道沒有任何快取訊息時移除索引資料。

    Args:
        channel_id: 頻道 ID
    """
    channel_messages = _channel_messages.get(channel_id)
    if channel_messages is not None and not channel_messages:
        _channel_messages.pop(channel_id, None)
        _channel_guild_map.pop(channel_id, None)


def prune_expired() -> None:
    """
    主動清除所有頻道中已超過 TTL 的訊息，供背景任務週期性呼叫。
    """
    for channel_id in list(_channel_messages.keys()):
        _evict_channel(channel_id)


def get_cached_message(channel_id: int, message_id: int) -> dict | None:
    """
    查詢快取中的訊息內容。

    Args:
        channel_id: 頻道 ID
        message_id: 訊息 ID

    Returns:
        dict，包含 author_id、content；找不到則回傳 None
    """
    channel_messages = _channel_messages.get(channel_id)
    if channel_messages is None:
        return None
    return channel_messages.get(message_id)


def remove_message(channel_id: int, message_id: int) -> None:
    """
    從快取中移除指定訊息，用於 raw delete 事件後清理。

    Args:
        channel_id: 頻道 ID
        message_id: 訊息 ID
    """
    global _total_entries
    channel_messages = _channel_messages.get(channel_id)
    if channel_messages is None:
        return
    removed_message = channel_messages.pop(message_id, None)
    if removed_message is not None:
        _total_entries -= 1
    _remove_channel_if_empty(channel_id)


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
        channel_messages = _channel_messages.pop(channel_id, None)
        if channel_messages:
            _total_entries -= len(channel_messages)
        _channel_guild_map.pop(channel_id, None)
