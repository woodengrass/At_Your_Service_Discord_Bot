class StorageLimitExceededError(Exception):
    """
    storage_set() 超過 key 長度、value 大小或每個安裝的 key 數量上限時拋出。
    """


class ScheduledTaskLimitExceededError(Exception):
    """
    schedule_task() 超過數量、payload 大小或時間範圍限制時拋出。
    """
