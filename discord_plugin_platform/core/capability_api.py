from dataclasses import dataclass, field
from typing import Any, Callable

# 每個函式對應的授權旗標，None 代表基本能力（隨安裝自動取得）
CAPABILITY_OWNERS: dict[str, str | None] = {
    "get_member": None,
    "get_channel": None,
    "get_guild_info": None,
    "get_role": None,
    "random": None,
    "send_message": "send_message",
    "reply_message": "send_message",
    "edit_message": "send_message",
    "pin_message": "send_message",
    "unpin_message": "send_message",
    "send_poll": "send_message",
    "schedule_task": "schedule_task",
    "cancel_scheduled_task": "schedule_task",
    "storage_get": "storage",
    "storage_set": "storage",
    "storage_delete": "storage",
    "storage_list_keys": "storage",
    "storage_get_leaderboard": "storage",
    "delete_message": "delete_message",
    "add_role": "manage_roles",
    "remove_role": "manage_roles",
    "get_member_role_ids": "manage_roles",
    "set_nickname": "manage_roles",
    "timeout_member": "moderate_members",
    "read_message_history": "read_message_history",
    "create_thread": "manage_threads",
    "archive_thread": "manage_threads",
}

# 呼叫當下就同步執行、外掛拿得到真實回傳值的函式名稱（其餘一律視為延後類，記進動作佇列）
SYNCHRONOUS_FUNCTIONS = {
    "get_member",
    "get_channel",
    "get_guild_info",
    "get_role",
    "random",
    "schedule_task",
    "cancel_scheduled_task",
    "storage_get",
    "storage_set",
    "storage_delete",
    "storage_list_keys",
    "storage_get_leaderboard",
    "get_member_role_ids",
    "read_message_history",
}

MAX_ACTIONS_PER_EXECUTION = 20


@dataclass
class ExecutionContext:
    """
    單次外掛執行期間共用的狀態，能力函式透過這個物件存取宿主資源、累積延後動作。
    """
    guild_id: int
    plugin_id: str
    granted_capabilities: set[str]
    action_queue: list[dict[str, Any]] = field(default_factory=list)

    def has_capability(self, capability_name: str) -> bool:
        """
        檢查這次執行的安裝是否有授權指定能力。

        Args:
            capability_name: 能力旗標名稱

        Returns:
            True 表示已授權
        """
        return capability_name is None or capability_name in self.granted_capabilities

    def queue_action(self, action_type: str, params: dict[str, Any]) -> None:
        """
        把一個延後類動作記進這次執行的動作佇列。

        Args:
            action_type: 動作類型，對應能力函式名稱
            params: 動作參數

        Raises:
            RuntimeError: 超過單次執行動作數量上限
        """
        if len(self.action_queue) >= MAX_ACTIONS_PER_EXECUTION:
            raise RuntimeError(f"單次執行動作數量超過上限（{MAX_ACTIONS_PER_EXECUTION}）")
        self.action_queue.append({"type": action_type, "params": params})


def get_allowed_functions(context: ExecutionContext) -> dict[str, Callable]:
    """
    依這次執行授權的能力範圍，回傳可以綁進沙箱的函式集合。

    Args:
        context: 這次執行的上下文

    Returns:
        dict，key 是函式名稱，value 是實際綁進 Lua 環境的 callable

    Raises:
        NotImplementedError: 目前僅完成介面骨架，實際函式綁定於第一階段沙箱引擎開發時實作
    """
    raise NotImplementedError("能力函式的實際綁定邏輯待第一階段沙箱引擎開發時實作，見 sandbox/capability_bindings.py")
