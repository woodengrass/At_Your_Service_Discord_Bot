from core import message_cache


def test_cache_message_replaces_existing_message() -> None:
    """
    重複快取同一則訊息時，應以最新內容取代舊內容。
    """
    message_cache.cache_message(1111, 2222, 3333, 4444, "old content")
    message_cache.cache_message(1111, 2222, 3333, 4444, "new content")

    cached_message = message_cache.get_cached_message(2222, 3333)

    assert cached_message["content"] == "new content"
    message_cache.purge_guild(1111)


def test_cache_message_evicts_oldest_entry_at_global_limit(monkeypatch) -> None:
    """
    全域上限滿時應淘汰最舊訊息，而不是永久停止快取新訊息。
    """
    monkeypatch.setattr(message_cache, "MAX_TOTAL_CACHE_ENTRIES", 1)
    message_cache.cache_message(1111, 2222, 3333, 4444, "old content")
    message_cache.cache_message(1111, 2222, 3334, 4444, "new content")

    assert message_cache.get_cached_message(2222, 3333) is None
    assert message_cache.get_cached_message(2222, 3334)["content"] == "new content"
    message_cache.purge_guild(1111)


def test_remove_message_deletes_cached_entry() -> None:
    """
    raw delete 事件後可移除單一訊息快取。
    """
    message_cache.cache_message(1111, 2222, 3333, 4444, "content")

    message_cache.remove_message(2222, 3333)

    assert message_cache.get_cached_message(2222, 3333) is None
