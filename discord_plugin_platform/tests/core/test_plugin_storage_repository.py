"""
core/plugin_storage_repository.py 的測試：storage_* 的基本讀寫，以及
key 長度／value 大小／每安裝 key 數量的濫用防護上限。
"""

import pytest

from core import database, plugin_storage_repository
from core.plugin_storage_repository import (
    MAX_LEADERBOARD_LIMIT,
    MAX_SCHEDULED_TASK_NAME_LENGTH,
    MAX_STORAGE_KEY_LENGTH,
    MAX_STORAGE_VALUE_BYTES,
    ScheduledTaskLimitExceededError,
    StorageLimitExceededError,
)


@pytest.fixture
async def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test_storage.db"))
    await database.init_db()
    yield
    await database.close_db()


async def test_storage_set_get_delete_roundtrip(temp_db):
    await plugin_storage_repository.storage_set(1, "plugin_a", "score", 42)
    assert await plugin_storage_repository.storage_get(1, "plugin_a", "score") == 42

    await plugin_storage_repository.storage_delete(1, "plugin_a", "score")
    assert await plugin_storage_repository.storage_get(1, "plugin_a", "score") is None


async def test_storage_set_rejects_key_over_length_limit(temp_db):
    with pytest.raises(StorageLimitExceededError, match="key 長度"):
        await plugin_storage_repository.storage_set(1, "plugin_a", "x" * (MAX_STORAGE_KEY_LENGTH + 1), 1)


async def test_storage_set_rejects_value_over_size_limit(temp_db):
    oversized_value = "x" * (MAX_STORAGE_VALUE_BYTES + 1)
    with pytest.raises(StorageLimitExceededError, match="value 大小"):
        await plugin_storage_repository.storage_set(1, "plugin_a", "big", oversized_value)


async def test_storage_set_rejects_new_key_once_installation_is_full(temp_db, monkeypatch):
    monkeypatch.setattr(plugin_storage_repository, "MAX_STORAGE_KEYS_PER_INSTALLATION", 2)

    await plugin_storage_repository.storage_set(1, "plugin_a", "key1", 1)
    await plugin_storage_repository.storage_set(1, "plugin_a", "key2", 2)

    with pytest.raises(StorageLimitExceededError, match="key 數量已達上限"):
        await plugin_storage_repository.storage_set(1, "plugin_a", "key3", 3)


async def test_storage_set_overwriting_existing_key_does_not_count_against_limit(temp_db, monkeypatch):
    """
    覆蓋既有 key（不是新增）不應該被算進數量上限，否則安裝滿了之後連更新
    自己既有的計數器都做不到。
    """
    monkeypatch.setattr(plugin_storage_repository, "MAX_STORAGE_KEYS_PER_INSTALLATION", 1)

    await plugin_storage_repository.storage_set(1, "plugin_a", "key1", 1)
    await plugin_storage_repository.storage_set(1, "plugin_a", "key1", 2)

    assert await plugin_storage_repository.storage_get(1, "plugin_a", "key1") == 2


async def test_storage_limits_are_scoped_per_guild_and_plugin(temp_db, monkeypatch):
    """
    數量上限是每個 (guild_id, plugin_id) 各自獨立計算，不同伺服器或不同外掛
    不應該互相搶額度。
    """
    monkeypatch.setattr(plugin_storage_repository, "MAX_STORAGE_KEYS_PER_INSTALLATION", 1)

    await plugin_storage_repository.storage_set(1, "plugin_a", "key1", 1)
    await plugin_storage_repository.storage_set(2, "plugin_a", "key1", 1)
    await plugin_storage_repository.storage_set(1, "plugin_b", "key1", 1)


async def test_storage_get_leaderboard_rejects_invalid_limit(temp_db):
    with pytest.raises(StorageLimitExceededError, match="leaderboard limit"):
        await plugin_storage_repository.storage_get_leaderboard(1, "plugin_a", "score", 0)
    with pytest.raises(StorageLimitExceededError, match="leaderboard limit"):
        await plugin_storage_repository.storage_get_leaderboard(
            1,
            "plugin_a",
            "score",
            MAX_LEADERBOARD_LIMIT + 1,
        )


async def test_create_scheduled_task_rejects_invalid_inputs(temp_db):
    with pytest.raises(ScheduledTaskLimitExceededError, match="delay_seconds"):
        await plugin_storage_repository.create_scheduled_task(1, "plugin_a", 0, "task", {})
    with pytest.raises(ScheduledTaskLimitExceededError, match="task_name"):
        await plugin_storage_repository.create_scheduled_task(
            1,
            "plugin_a",
            60,
            "x" * (MAX_SCHEDULED_TASK_NAME_LENGTH + 1),
            {},
        )
    with pytest.raises(ScheduledTaskLimitExceededError, match="recurring_interval_seconds"):
        await plugin_storage_repository.create_scheduled_task(1, "plugin_a", 60, "task", {}, 1)


async def test_create_scheduled_task_rejects_too_many_tasks(temp_db, monkeypatch):
    monkeypatch.setattr(plugin_storage_repository, "MAX_SCHEDULED_TASKS_PER_INSTALLATION", 1)

    await plugin_storage_repository.create_scheduled_task(1, "plugin_a", 60, "task", {})

    with pytest.raises(ScheduledTaskLimitExceededError, match="排程任務數量已達上限"):
        await plugin_storage_repository.create_scheduled_task(1, "plugin_a", 60, "task", {})
