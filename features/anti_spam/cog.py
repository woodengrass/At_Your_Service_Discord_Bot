import asyncio
import datetime
import logging
from collections import deque

import discord
from discord.ext import commands, tasks

from core.audit_log_repository import add_log_entry
from core.config import CONFIG
from core.guild_settings import GuildSettings
from core.i18n import i18n

logger = logging.getLogger(__name__)

# 讀取全域設定 (預設值)
spam_config = CONFIG.get("anti_spam", {})
SPAM_TIME_WINDOW = spam_config.get("time_window_seconds", 120)
SPAM_CHANNEL_THRESHOLD = spam_config.get("channel_threshold", 5)  # 跨頻道閾值
SPAM_SAME_CHANNEL_THRESHOLD = spam_config.get("same_channel_threshold", 5)  # 同頻道閾值
TIMEOUT_HOURS = spam_config.get("timeout_hours", 240)
TIMEOUT_DURATION = datetime.timedelta(hours=TIMEOUT_HOURS)
CLEANUP_INTERVAL = spam_config.get("cleanup_interval_minutes", 30)

# 預設開關 (如果 config 沒寫，預設都開啟)
DEFAULT_MULTI_ENABLED = spam_config.get("enable_multi_channel", True)
DEFAULT_SAME_ENABLED = spam_config.get("enable_same_channel", True)
ALLOWED_CHANNEL_IDS_KEY = "allowed_channel_ids"


class AntiSpam(commands.Cog):
    """
    偵測使用者跨頻道或同頻道重複洗版行為，並自動禁言、刪除訊息與記錄違規。
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.message_history: dict[tuple[int, int], deque] = {}
        self.cleanup_task.change_interval(minutes=CLEANUP_INTERVAL)
        self.cleanup_task.start()

    def cog_unload(self) -> None:
        """
        卸載 Cog 時停止清理背景任務。
        """
        self.cleanup_task.cancel()

    def is_whitelisted(self, user_id: int, guild_id: int) -> bool:
        """
        檢查使用者是否在白名單中。

        Args:
            user_id: 使用者 ID
            guild_id: 伺服器 ID

        Returns:
            True 表示使用者在白名單中
        """
        whitelist = GuildSettings.get_whitelist(guild_id)
        return str(user_id) in whitelist

    def get_announcement_channel(self, guild_id: int) -> str | None:
        """
        取得伺服器的公告日誌頻道 ID。

        Args:
            guild_id: 伺服器 ID

        Returns:
            頻道 ID 字串，若尚未設定則回傳 None
        """
        return GuildSettings.get_log_channel(guild_id)

    def is_allowed_channel(self, channel_id: int, module_config: dict) -> bool:
        """
        檢查指定頻道是否被設定為防洗版允許頻道。

        Args:
            channel_id: 頻道 ID
            module_config: 防洗版模組設定

        Returns:
            True 表示該頻道不受防洗版偵測約束
        """
        allowed_channel_ids = module_config.get(ALLOWED_CHANNEL_IDS_KEY, [])
        return str(channel_id) in {str(allowed_channel_id) for allowed_channel_id in allowed_channel_ids}

    @tasks.loop(minutes=CLEANUP_INTERVAL)
    async def cleanup_task(self) -> None:
        """
        定期清除已超過偵測時間窗、不再需要追蹤的使用者訊息紀錄。
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        expired_keys = []

        for history_key, history in self.message_history.items():
            if not history or (now - history[-1]["time"]).total_seconds() > SPAM_TIME_WINDOW:
                expired_keys.append(history_key)

        for history_key in expired_keys:
            del self.message_history[history_key]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """
        監聽訊息，統計使用者近期發言以偵測跨頻道或同頻道洗版行為。

        Args:
            message: 收到的訊息物件
        """
        if message.author.bot or not message.guild:
            return

        module_config = GuildSettings.get_module_config(message.guild.id, "anti_spam")

        if not module_config.get("enabled", True):
            return

        if self.is_allowed_channel(message.channel.id, module_config):
            return

        if self.is_whitelisted(message.author.id, message.guild.id):
            return

        # 取得細項開關 (優先讀取 DB，若無則使用全域預設值)
        enable_multi = module_config.get("enable_multi_channel", DEFAULT_MULTI_ENABLED)
        enable_same = module_config.get("enable_same_channel", DEFAULT_SAME_ENABLED)

        if not enable_multi and not enable_same:
            return  # 如果兩個子功能都關閉，就不用記錄了

        now = datetime.datetime.now(datetime.timezone.utc)
        history_key = (message.guild.id, message.author.id)

        # 初始化與紀錄
        if history_key not in self.message_history:
            history_limit = max(SPAM_SAME_CHANNEL_THRESHOLD, SPAM_CHANNEL_THRESHOLD) + 5
            self.message_history[history_key] = deque(maxlen=history_limit)

        self.message_history[history_key].append({
            "content": message.content,
            "channel_id": message.channel.id,
            "time": now,
            "message_obj": message
        })

        # 清理過期訊息
        while self.message_history[history_key] and (
                now - self.message_history[history_key][0]["time"]).total_seconds() > SPAM_TIME_WINDOW:
            self.message_history[history_key].popleft()

        current_content = message.content
        if not current_content.strip():
            return

        # 統計
        targets = []
        channel_counts = {}

        for entry in self.message_history[history_key]:
            if entry["content"] == current_content:
                targets.append(entry)
                channel_id = entry["channel_id"]
                channel_counts[channel_id] = channel_counts.get(channel_id, 0) + 1

        unique_channels = len(channel_counts)
        max_repeats = max(channel_counts.values()) if channel_counts else 0

        triggered = False

        if enable_multi and unique_channels >= SPAM_CHANNEL_THRESHOLD:
            await self.take_action(message, targets, spam_type="multi", count=unique_channels)
            triggered = True

        elif enable_same and max_repeats >= SPAM_SAME_CHANNEL_THRESHOLD:
            await self.take_action(message, targets, spam_type="single", count=max_repeats)
            triggered = True

        if triggered:
            self.message_history[history_key].clear()

    async def take_action(self, message: discord.Message, targets: list[dict], spam_type: str, count: int) -> None:
        """
        對洗版使用者採取禁言、刪除訊息並記錄違規等處置。

        Args:
            message: 觸發偵測的訊息物件
            targets: 判定為洗版的歷史訊息紀錄列表
            spam_type: 洗版類型，"multi" 為跨頻道，"single" 為同頻道
            count: 觸發偵測的重複次數
        """
        user = message.author
        guild = message.guild
        logger.warning(
            "偵測到洗版：伺服器=%s (%s)，頻道=%s (%s)，使用者=%s (%s)，類型=%s，次數=%s",
            guild.name,
            guild.id,
            message.channel,
            message.channel.id,
            user,
            user.id,
            spam_type,
            count,
        )

        # 1. 禁言
        try:
            if guild.me.guild_permissions.moderate_members:
                reason_key = (
                    "messages.spam_timeout_reason"
                    if spam_type == "multi"
                    else "messages.spam_same_channel_reason"
                )
                reason = i18n.get_text(reason_key, guild.id)
                await user.timeout(TIMEOUT_DURATION, reason=reason)
                await add_log_entry(guild.id, user.id, "spam_timeout", reason)
                logger.info(
                    "洗版使用者禁言成功：伺服器 ID=%s，使用者 ID=%s，禁言時數=%s",
                    guild.id,
                    user.id,
                    TIMEOUT_HOURS,
                )
            else:
                logger.warning("缺少禁言成員權限：伺服器 ID=%s，使用者 ID=%s", guild.id, user.id)
        except Exception as error:
            logger.error(f"禁言洗版使用者失敗：{error}", exc_info=True)

        # 2. 刪除訊息
        delete_tasks = [target["message_obj"].delete() for target in targets]

        if delete_tasks:
            results = await asyncio.gather(*delete_tasks, return_exceptions=True)
            deleted_count = 0
            for result in results:
                if isinstance(result, BaseException):
                    logger.error(
                        f"刪除洗版訊息失敗：{result}",
                        exc_info=(type(result), result, result.__traceback__),
                    )
                else:
                    deleted_count += 1
        else:
            deleted_count = 0

        logger.info(
            "洗版訊息刪除完成：伺服器 ID=%s，使用者 ID=%s，成功=%s，失敗=%s",
            guild.id,
            user.id,
            deleted_count,
            len(targets) - deleted_count,
        )

        # 3. 獨立紀錄 Log
        announcement_id = self.get_announcement_channel(guild.id)
        if announcement_id:
            channel = guild.get_channel(int(announcement_id))
            if channel:
                content_preview = message.content[:100].replace("`", "ˋ")

                if spam_type == "multi":
                    # 跨頻道 Log
                    log_message = i18n.get_text("messages.spam_detected_log", guild.id,
                                                 user=user.mention, count=count, content=content_preview)
                else:
                    # 單一頻道 Log
                    log_message = i18n.get_text("messages.spam_detected_single_log", guild.id,
                                                 user=user.mention, channel=message.channel.mention,
                                                 count=count, content=content_preview)

                try:
                    await channel.send(log_message + f"\n(已清除 {deleted_count} 則相關訊息)")
                    logger.info(
                        "洗版通知發送成功：伺服器 ID=%s，紀錄頻道 ID=%s",
                        guild.id,
                        channel.id,
                    )
                except Exception as error:
                    logger.error(f"發送洗版通知失敗：{error}", exc_info=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AntiSpam(bot))

