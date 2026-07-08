"""
掛在既有 discord.py bot 上的事件監聽器，轉發進 core/dispatcher.py。
第二階段開發重點（接上真正的 bot），見 design.md 第二階段。
"""

import datetime
import json
import logging

import discord
from discord.ext import commands, tasks

from core import bot_registry, message_cache, repository
from core.dispatcher import dispatch_event

logger = logging.getLogger(__name__)

MESSAGE_CACHE_EVENTS = {"on_message_edit", "on_message_delete"}


class PluginPlatformListeners(commands.Cog):
    """
    監聽 Discord 事件並轉發給外掛平台的 dispatcher。
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        """
        載入 Cog 時啟動排程任務消費迴圈。
        """
        self.scheduled_task_loop.start()

    def cog_unload(self) -> None:
        """
        卸載 Cog 時停止排程任務消費迴圈。
        """
        self.scheduled_task_loop.cancel()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """
        轉發訊息事件給 dispatcher，交由已安裝且訂閱此事件的外掛處理。

        Args:
            message: Discord 訊息物件
        """
        if message.author.bot or not message.guild:
            return
        if await repository.guild_has_event_subscription(message.guild.id, MESSAGE_CACHE_EVENTS):
            message_cache.cache_message(
                guild_id=message.guild.id,
                channel_id=message.channel.id,
                message_id=message.id,
                author_id=message.author.id,
                content=message.content,
            )
        await dispatch_event(
            message.guild.id,
            "on_message",
            {
                "message_id": message.id,
                "author_id": message.author.id,
                "channel_id": message.channel.id,
                "content": message.content,
                "created_at": message.created_at.isoformat(),
            },
        )

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """
        轉發按鈕與選單互動事件給 dispatcher。

        Args:
            interaction: Discord 互動物件
        """
        if interaction.guild is None or interaction.type != discord.InteractionType.component:
            return
        interaction_data = interaction.data if isinstance(interaction.data, dict) else {}
        await dispatch_event(
            interaction.guild.id,
            "on_interaction",
            {
                "interaction_type": _get_interaction_component_type(interaction_data),
                "custom_id": interaction_data.get("custom_id"),
                "values": interaction_data.get("values"),
                "invoking_user_id": interaction.user.id,
                "message_id": interaction.message.id if interaction.message else None,
            },
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """
        轉發成員加入事件給 dispatcher。

        Args:
            member: 加入伺服器的成員
        """
        await dispatch_event(
            member.guild.id,
            "on_member_join",
            {
                "user_id": member.id,
                "joined_at": member.joined_at.isoformat() if member.joined_at else None,
            },
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """
        轉發成員離開事件給 dispatcher。

        Args:
            member: 離開伺服器的成員
        """
        await dispatch_event(
            member.guild.id,
            "on_member_leave",
            {
                "user_id": member.id,
                "joined_at": member.joined_at.isoformat() if member.joined_at else None,
            },
        )

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        """
        使用 raw 事件轉發訊息編輯，避免 discord.py 快取未命中時漏事件。

        Args:
            payload: Discord raw message update payload
        """
        if payload.guild_id is None:
            return
        cached_message = message_cache.get_cached_message(payload.channel_id, payload.message_id)
        await dispatch_event(
            payload.guild_id,
            "on_message_edit",
            {
                "message_id": payload.message_id,
                "channel_id": payload.channel_id,
                "author_id": cached_message["author_id"] if cached_message else None,
                "old_content": cached_message["content"] if cached_message else None,
                "new_content": payload.data.get("content"),
                "edited_at": payload.data.get("edited_timestamp"),
            },
        )
        if cached_message and payload.data.get("content") is not None:
            message_cache.cache_message(
                guild_id=payload.guild_id,
                channel_id=payload.channel_id,
                message_id=payload.message_id,
                author_id=cached_message["author_id"],
                content=payload.data["content"],
            )

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        """
        使用 raw 事件轉發訊息刪除，避免 discord.py 快取未命中時漏事件。

        Args:
            payload: Discord raw message delete payload
        """
        if payload.guild_id is None:
            return
        cached_message = message_cache.get_cached_message(payload.channel_id, payload.message_id)
        await dispatch_event(
            payload.guild_id,
            "on_message_delete",
            {
                "message_id": payload.message_id,
                "channel_id": payload.channel_id,
                "author_id": cached_message["author_id"] if cached_message else None,
                "content": cached_message["content"] if cached_message else None,
                "deleted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            },
        )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """
        轉發語音狀態變更事件給 dispatcher。

        Args:
            member: 狀態變更的成員
            before: 變更前語音狀態
            after: 變更後語音狀態
        """
        await dispatch_event(
            member.guild.id,
            "on_voice_state_update",
            {
                "user_id": member.id,
                "before_channel_id": before.channel.id if before.channel else None,
                "after_channel_id": after.channel.id if after.channel else None,
            },
        )

    @tasks.loop(minutes=1)
    async def scheduled_task_loop(self) -> None:
        """
        每分鐘消費已到期的外掛排程任務，並轉發成 on_scheduled_task 事件。
        """
        await self.bot.wait_until_ready()
        await self.consume_due_scheduled_tasks()

    async def consume_due_scheduled_tasks(self) -> None:
        """
        消費目前所有已到期的外掛排程任務。
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        due_tasks = await repository.get_due_scheduled_tasks(now.isoformat())
        for scheduled_task in due_tasks:
            try:
                task_payload = json.loads(scheduled_task["payload_json"])
                await dispatch_event(
                    scheduled_task["guild_id"],
                    "on_scheduled_task",
                    {
                        "task_name": task_payload["task_name"],
                        "payload": task_payload["payload"],
                    },
                )
                recurring_interval_seconds = scheduled_task["recurring_interval_seconds"]
                if recurring_interval_seconds is None:
                    await repository.delete_scheduled_task(scheduled_task["task_id"])
                else:
                    next_run_at = _calculate_next_run_at(
                        scheduled_task["run_at"], recurring_interval_seconds
                    )
                    await repository.update_scheduled_task_run_at(scheduled_task["task_id"], next_run_at)
            except Exception as error:
                logger.error(f"處理外掛排程任務失敗：{error}", exc_info=True)


def _get_interaction_component_type(interaction_data: dict) -> str:
    """
    將 Discord component_type 轉成外掛 API 使用的互動類型文字。

    Args:
        interaction_data: Discord interaction data dict

    Returns:
        button、select_menu 或 unknown
    """
    component_type = interaction_data.get("component_type")
    if component_type == 2:
        return "button"
    if component_type in {3, 5, 6, 7, 8}:
        return "select_menu"
    return "unknown"


def _calculate_next_run_at(run_at: str, recurring_interval_seconds: int) -> str:
    """
    計算週期性任務下一次執行時間。

    Args:
        run_at: 這次到期時間 ISO 8601 字串
        recurring_interval_seconds: 週期秒數

    Returns:
        下一次執行時間 ISO 8601 字串
    """
    current_run_at = datetime.datetime.fromisoformat(run_at)
    return (current_run_at + datetime.timedelta(seconds=recurring_interval_seconds)).isoformat()


async def setup(bot: commands.Bot) -> None:
    bot_registry.set_bot(bot)
    await bot.add_cog(PluginPlatformListeners(bot))
