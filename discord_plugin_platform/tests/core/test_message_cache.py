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
