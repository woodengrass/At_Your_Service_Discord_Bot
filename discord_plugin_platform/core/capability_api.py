import asyncio
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

import aiosqlite
import discord

from core import plugin_storage_repository

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

# read_message_history 的獨立頻率限制：每個 (guild_id, plugin_id) 每分鐘最多呼叫幾次，
# 跟執行/動作配額（core/quota.py）分開算，見 design.md 附錄 A.3.4。
READ_MESSAGE_HISTORY_LIMIT_PER_MINUTE = 10
READ_MESSAGE_HISTORY_MAX_MESSAGES = 30
_READ_MESSAGE_HISTORY_WINDOW_SECONDS = 60
_read_message_history_timestamps: dict[tuple[int, str], deque] = {}

# 跨執行緒呼叫主 event loop 上的 coroutine 時，最多等待幾秒才視為逾時，
# 避免主 loop 異常忙碌時，沙箱這次執行被無限期卡住。
CROSS_THREAD_CALL_TIMEOUT_SECONDS = 5.0


@dataclass
class ExecutionContext:
    """
    單次外掛執行期間共用的狀態，能力函式透過這個物件存取宿主資源、累積延後動作。

    沙箱本身（sandbox/engine.py）是同步、CPU-bound 的執行，實際透過
    `loop.run_in_executor()` 丟到背景執行緒跑（見 sandbox/worker.py），
    但需要讀寫資料庫或呼叫 discord.py 的能力函式，底層都是 async 的。
    `event_loop` 就是用來讓這些同步的能力函式透過
    `asyncio.run_coroutine_threadsafe()` 把 coroutine 丟回主 event loop 執行、
    同步等待結果，藉此在不違反 Lua 呼叫必須是同步函式這個限制下，
    安全地共用同一個 aiosqlite 連線與同一個 discord.py Client，
    不需要另外為沙箱開一條獨立的資料庫連線或行程。

    `execution_db` 是例外：只有這次安裝有 `storage`／`schedule_task` 能力時，
    `core/dispatcher.py` 才會開一條這次執行專用的連線傳進來，storage/schedule
    相關的能力函式要用這條連線，不是上面說的共用連線——這樣 dispatcher 才能
    在驗證動作清單之後，依結果決定 commit 或 rollback 這次執行寫入的資料，
    不會被平台上其他地方對共用連線的 commit() 干擾（原本用 SAVEPOINT 做這件事，
    實測發現 SQLite 的交易狀態是連線層級的，任何地方對共用連線的 commit() 都會
    把 SAVEPOINT 沖掉，因此改成這次執行專用的獨立連線，見 design.md 第 5.4.2 節）。
    沒有這兩個能力的安裝，這個欄位是 None，storage/schedule 函式根本不會被綁進去。
    """

    guild_id: int
    plugin_id: str
    granted_capabilities: set[str]
    bot: discord.Client
    event_loop: asyncio.AbstractEventLoop
    execution_db: aiosqlite.Connection | None = None
    action_queue: list[dict[str, Any]] = field(default_factory=list)

    def has_capability(self, capability_name: str | None) -> bool:
        """
        檢查這次執行的安裝是否有授權指定能力。

        Args:
            capability_name: 能力旗標名稱，None 代表基本能力

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

    def run_coroutine_sync(self, coro: Any) -> Any:
        """
        把一個 coroutine 丟回主 event loop 執行，同步等待結果後回傳。

        Args:
            coro: 要執行的 coroutine（例如 storage_repository 或 discord.py 的 async 呼叫）

        Returns:
            coroutine 執行完的回傳值

        Raises:
            TimeoutError: 超過 CROSS_THREAD_CALL_TIMEOUT_SECONDS 秒還沒有結果
        """
        future = asyncio.run_coroutine_threadsafe(coro, self.event_loop)
        return future.result(timeout=CROSS_THREAD_CALL_TIMEOUT_SECONDS)

    def get_guild(self) -> discord.Guild | None:
        """
        取得這次執行所屬的伺服器物件，找不到（例如 bot 已被踢出）回傳 None。

        Returns:
            discord.Guild 或 None
        """
        return self.bot.get_guild(self.guild_id)


def _check_read_message_history_rate_limit(guild_id: int, plugin_id: str) -> bool:
    """
    檢查並消耗一次 read_message_history 頻率限制配額，用完回傳 False 且不消耗。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID

    Returns:
        True 表示額度足夠、已計入這次呼叫
    """
    key = (guild_id, plugin_id)
    timestamps = _read_message_history_timestamps.setdefault(key, deque())
    now = time.time()
    while timestamps and now - timestamps[0] > _READ_MESSAGE_HISTORY_WINDOW_SECONDS:
        timestamps.popleft()

    if len(timestamps) >= READ_MESSAGE_HISTORY_LIMIT_PER_MINUTE:
        return False

    timestamps.append(now)
    return True


def _member_to_dict(member: discord.Member) -> dict[str, Any]:
    """
    把 discord.Member 轉成外掛看得到的最小必要欄位，不外洩完整物件。

    Args:
        member: discord.py 的 Member 物件

    Returns:
        dict，包含 id、暱稱、加入時間、身分組 ID 清單、是否為機器人
    """
    return {
        "id": member.id,
        "nickname": member.display_name,
        "joined_at": member.joined_at.isoformat() if member.joined_at else None,
        "role_ids": [role.id for role in member.roles],
        "is_bot": member.bot,
    }


def _build_basic_functions(context: ExecutionContext) -> dict[str, Callable]:
    """
    建立 A.1 基本能力函式（隨安裝自動取得，不需要同意畫面）。

    限制：查詢一律只能查安裝所在伺服器內的物件，所以底下每個函式都是從
    `context.get_guild()` 反查，不接受外掛傳入任意 guild_id。

    Args:
        context: 這次執行的上下文

    Returns:
        函式名稱到 callable 的對照表
    """

    def get_member(user_id: int) -> dict | None:
        guild = context.get_guild()
        if guild is None:
            return None
        member = guild.get_member(user_id)
        return _member_to_dict(member) if member else None

    def get_channel(channel_id: int) -> dict | None:
        guild = context.get_guild()
        if guild is None:
            return None
        channel = guild.get_channel(channel_id)
        if channel is None:
            return None
        return {"id": channel.id, "name": channel.name, "type": str(channel.type)}

    def get_guild_info() -> dict | None:
        guild = context.get_guild()
        if guild is None:
            return None
        return {"id": guild.id, "name": guild.name, "member_count": guild.member_count}

    def get_role(role_id: int) -> dict | None:
        guild = context.get_guild()
        if guild is None:
            return None
        role = guild.get_role(role_id)
        if role is None:
            return None
        return {"id": role.id, "name": role.name, "position": role.position}

    def random_between(min_value: int, max_value: int) -> int:
        return random.randint(min_value, max_value)

    return {
        "get_member": get_member,
        "get_channel": get_channel,
        "get_guild_info": get_guild_info,
        "get_role": get_role,
        "random": random_between,
    }


def _build_message_functions(context: ExecutionContext) -> dict[str, Callable]:
    """
    建立 A.2.1 send_message 群組能力函式，全部是延後類，記進動作佇列。

    Args:
        context: 這次執行的上下文

    Returns:
        函式名稱到 callable 的對照表
    """
    return {
        "send_message": lambda channel_id, content, embed=None, buttons=None: context.queue_action(
            "send_message", {"channel_id": channel_id, "content": content, "embed": embed, "buttons": buttons}
        ),
        # reply_message/edit_message/pin_message/unpin_message 都多帶了 channel_id（附錄 A.2.1
        # 原本只有 message_id）：discord.py 沒有「只憑 message_id 跨頻道查訊息」的 API，一定要先
        # 知道頻道才能 fetch_message()；這是接 core/dispatcher.py 的 _execute_actions() 時才發現的
        # 缺口，若不補這個參數，宿主端只能挨個把整個伺服器的頻道都掃過一輪找訊息，既慢又不可靠。
        "reply_message": lambda channel_id, message_id, content, embed=None, buttons=None: context.queue_action(
            "reply_message",
            {
                "channel_id": channel_id,
                "message_id": message_id,
                "content": content,
                "embed": embed,
                "buttons": buttons,
            },
        ),
        "edit_message": lambda channel_id, message_id, content, embed=None: context.queue_action(
            "edit_message", {"channel_id": channel_id, "message_id": message_id, "content": content, "embed": embed}
        ),
        "pin_message": lambda channel_id, message_id: context.queue_action(
            "pin_message", {"channel_id": channel_id, "message_id": message_id}
        ),
        "unpin_message": lambda channel_id, message_id: context.queue_action(
            "unpin_message", {"channel_id": channel_id, "message_id": message_id}
        ),
        "send_poll": lambda channel_id, question, options, duration: context.queue_action(
            "send_poll", {"channel_id": channel_id, "question": question, "options": options, "duration": duration}
        ),
    }


def _build_schedule_functions(context: ExecutionContext) -> dict[str, Callable]:
    """
    建立 A.2.2 schedule_task 群組能力函式，都是同步（本機 DB 操作，非 Discord API）。

    Args:
        context: 這次執行的上下文

    Returns:
        函式名稱到 callable 的對照表
    """

    def schedule_task(
        delay_seconds: float, task_name: str, payload: dict, recurring_interval_seconds: int | None = None
    ) -> str:
        return context.run_coroutine_sync(
            plugin_storage_repository.create_scheduled_task(
                context.guild_id,
                context.plugin_id,
                delay_seconds,
                task_name,
                payload,
                recurring_interval_seconds,
                db=context.execution_db,
            )
        )

    def cancel_scheduled_task(task_id: str) -> bool:
        return context.run_coroutine_sync(
            plugin_storage_repository.cancel_scheduled_task(
                context.guild_id, context.plugin_id, task_id, db=context.execution_db
            )
        )

    return {"schedule_task": schedule_task, "cancel_scheduled_task": cancel_scheduled_task}


def _build_storage_functions(context: ExecutionContext) -> dict[str, Callable]:
    """
    建立 A.2.3 storage 群組能力函式，都是同步，以 (guild_id, plugin_id, key) 隔離資料。

    Args:
        context: 這次執行的上下文

    Returns:
        函式名稱到 callable 的對照表
    """

    def storage_get(key: str) -> Any:
        return context.run_coroutine_sync(
            plugin_storage_repository.storage_get(context.guild_id, context.plugin_id, key, db=context.execution_db)
        )

    def storage_set(key: str, value: Any) -> None:
        context.run_coroutine_sync(
            plugin_storage_repository.storage_set(
                context.guild_id, context.plugin_id, key, value, db=context.execution_db
            )
        )

    def storage_delete(key: str) -> None:
        context.run_coroutine_sync(
            plugin_storage_repository.storage_delete(
                context.guild_id, context.plugin_id, key, db=context.execution_db
            )
        )

    def storage_list_keys(prefix: str = "") -> list[str]:
        return context.run_coroutine_sync(
            plugin_storage_repository.storage_list_keys(
                context.guild_id, context.plugin_id, prefix, db=context.execution_db
            )
        )

    def storage_get_leaderboard(prefix: str, limit: int) -> list[dict]:
        return context.run_coroutine_sync(
            plugin_storage_repository.storage_get_leaderboard(
                context.guild_id, context.plugin_id, prefix, limit, db=context.execution_db
            )
        )

    return {
        "storage_get": storage_get,
        "storage_set": storage_set,
        "storage_delete": storage_delete,
        "storage_list_keys": storage_list_keys,
        "storage_get_leaderboard": storage_get_leaderboard,
    }


def _build_high_risk_functions(context: ExecutionContext) -> dict[str, Callable]:
    """
    建立 A.3 高風險能力函式。`delete_message`／`manage_roles`／`moderate_members`／
    `manage_threads` 群組都是延後類；`get_member_role_ids`／`read_message_history`
    是同步類，各自對應附錄 A.3 的執行模式標註。

    Args:
        context: 這次執行的上下文

    Returns:
        函式名稱到 callable 的對照表
    """

    def get_member_role_ids(user_id: int) -> list[int] | None:
        guild = context.get_guild()
        if guild is None:
            return None
        member = guild.get_member(user_id)
        if member is None:
            return None
        return [role.id for role in member.roles]

    async def _fetch_message_history(channel_id: int, limit: int) -> list[dict]:
        guild = context.get_guild()
        if guild is None:
            return []
        channel = guild.get_channel(channel_id)
        if channel is None or not isinstance(channel, discord.abc.Messageable):
            return []
        capped_limit = min(limit, READ_MESSAGE_HISTORY_MAX_MESSAGES)
        messages = []
        async for message in channel.history(limit=capped_limit):
            messages.append(
                {
                    "message_id": message.id,
                    "author_id": message.author.id,
                    "content": message.content,
                    "created_at": message.created_at.isoformat(),
                }
            )
        return messages

    def read_message_history(channel_id: int, limit: int) -> list[dict]:
        if not _check_read_message_history_rate_limit(context.guild_id, context.plugin_id):
            raise RuntimeError(
                f"read_message_history 呼叫頻率超過上限（每分鐘 {READ_MESSAGE_HISTORY_LIMIT_PER_MINUTE} 次）"
            )
        return context.run_coroutine_sync(_fetch_message_history(channel_id, limit))

    return {
        "delete_message": lambda channel_id, message_id: context.queue_action(
            "delete_message", {"channel_id": channel_id, "message_id": message_id}
        ),
        "add_role": lambda user_id, role_id: context.queue_action(
            "add_role", {"user_id": user_id, "role_id": role_id}
        ),
        "remove_role": lambda user_id, role_id: context.queue_action(
            "remove_role", {"user_id": user_id, "role_id": role_id}
        ),
        "get_member_role_ids": get_member_role_ids,
        "set_nickname": lambda user_id, nickname: context.queue_action(
            "set_nickname", {"user_id": user_id, "nickname": nickname}
        ),
        "timeout_member": lambda user_id, duration_seconds, reason: context.queue_action(
            "timeout_member", {"user_id": user_id, "duration_seconds": duration_seconds, "reason": reason}
        ),
        "read_message_history": read_message_history,
        "create_thread": lambda channel_id, name: context.queue_action(
            "create_thread", {"channel_id": channel_id, "name": name}
        ),
        "archive_thread": lambda thread_id: context.queue_action("archive_thread", {"thread_id": thread_id}),
    }


def get_allowed_functions(context: ExecutionContext) -> dict[str, Callable]:
    """
    依這次執行授權的能力範圍，回傳可以綁進沙箱的函式集合。

    做法：先組出全部函式的完整對照表（基本能力一定包含；一般風險/高風險能力
    各自依 CAPABILITY_OWNERS 判斷 context 是否有授權，沒授權的函式完全不出現
    在回傳結果裡，而不是回傳一個「呼叫了會報錯」的空殼函式）。

    Args:
        context: 這次執行的上下文

    Returns:
        dict，key 是函式名稱，value 是實際綁進 Lua 環境的 callable，
        只包含這次執行有權限使用的函式
    """
    all_functions: dict[str, Callable] = {}
    all_functions.update(_build_basic_functions(context))
    all_functions.update(_build_message_functions(context))
    all_functions.update(_build_schedule_functions(context))
    all_functions.update(_build_storage_functions(context))
    all_functions.update(_build_high_risk_functions(context))

    return {
        function_name: function
        for function_name, function in all_functions.items()
        if context.has_capability(CAPABILITY_OWNERS.get(function_name))
    }
