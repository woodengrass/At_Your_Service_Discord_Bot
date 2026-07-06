import asyncio
import datetime
import logging
import re

import discord

logger = logging.getLogger(__name__)

BOT_LIKE_USERNAME_PATTERN = re.compile(r'^[a-zA-Z]+\d{3,}$')
LOCKDOWN_API_DELAY_SECONDS = 0.5


def calculate_risk_score(member: discord.Member, new_account_days: int) -> int:
    """
    依帳號年齡、頭像與使用者名稱格式計算被動風險分數，完全不需要成員做任何操作。

    Args:
        member: 加入的成員物件
        new_account_days: 視為「新帳號」的天數門檻

    Returns:
        風險分數，分數越高代表越可疑
    """
    score = 0

    account_age_days = (datetime.datetime.now(datetime.timezone.utc) - member.created_at).days
    if account_age_days < new_account_days:
        score += 2

    if member.avatar is None:
        score += 1

    if BOT_LIKE_USERNAME_PATTERN.match(member.name):
        score += 1

    return score


def _is_hidden_from_everyone(channel: discord.abc.GuildChannel) -> bool:
    """
    判斷頻道是否原本就已經設定成 @everyone 無法檢視（例如管理員專用頻道），
    這種頻道不應該被驗證系統的批次鎖定流程碰到。

    Args:
        channel: 要檢查的頻道物件

    Returns:
        True 代表 @everyone 已被擋在檢視權限外（含繼承自分類的設定）
    """
    everyone = channel.guild.default_role
    overwrite = channel.overwrites_for(everyone)
    if overwrite.view_channel is False:
        return True
    if overwrite.view_channel is None and channel.category is not None:
        category_overwrite = channel.category.overwrites_for(everyone)
        if category_overwrite.view_channel is False:
            return True
    return False


def _is_announcement_channel(channel: discord.abc.GuildChannel) -> bool:
    """
    判斷頻道是否為公告頻道（Announcement Channel）。
    公告頻道本來就只有管理員/特定身分組能發言，一般人只能訂閱追蹤，不應被驗證系統的批次鎖定流程碰到。

    Args:
        channel: 要檢查的頻道物件

    Returns:
        True 代表這是公告頻道
    """
    return isinstance(channel, discord.TextChannel) and channel.is_news()


def _copy_overwrite(overwrite: discord.PermissionOverwrite | None) -> discord.PermissionOverwrite | None:
    """
    複製頻道權限覆寫，避免後續修改同一物件時破壞回復用快照。

    Args:
        overwrite: 原始權限覆寫；目標原本沒有獨立覆寫時為 None

    Returns:
        複製後的權限覆寫；輸入為 None 時回傳 None
    """
    if overwrite is None:
        return None
    allow, deny = overwrite.pair()
    return discord.PermissionOverwrite.from_pair(allow, deny)


async def _rollback_lockdown(
    guild: discord.Guild,
    verified_role: discord.Role,
    granted_members: list[discord.Member],
    channel_snapshots: list[
        tuple[discord.TextChannel, discord.PermissionOverwrite | None, discord.PermissionOverwrite | None]
    ],
) -> int:
    """
    回復本次驗證系統啟用流程已完成的身分組與頻道權限變更。

    Args:
        guild: Discord 伺服器
        verified_role: 本次授予的已驗證身分組
        granted_members: 本次成功授予身分組的成員
        channel_snapshots: 本次修改過的頻道及修改前權限快照

    Returns:
        回復失敗的操作數量
    """
    failure_count = 0
    for channel, everyone_overwrite, verified_overwrite in reversed(channel_snapshots):
        overwrite_targets = (
            (guild.default_role, everyone_overwrite),
            (verified_role, verified_overwrite),
        )
        for target, overwrite in overwrite_targets:
            try:
                await channel.set_permissions(target, overwrite=overwrite)
            except Exception as error:
                failure_count += 1
                logger.error(
                    f"回復驗證頻道權限失敗（頻道 ID={channel.id}，目標 ID={target.id}）：{error}",
                    exc_info=True,
                )
        await asyncio.sleep(LOCKDOWN_API_DELAY_SECONDS)

    for member in reversed(granted_members):
        try:
            await member.remove_roles(verified_role, reason="驗證系統啟用失敗，回復本次變更")
        except Exception as error:
            failure_count += 1
            logger.error(f"回復既有成員身分組失敗（成員 ID={member.id}）：{error}", exc_info=True)
        await asyncio.sleep(LOCKDOWN_API_DELAY_SECONDS)

    return failure_count


async def lockdown_and_grandfather(
    guild: discord.Guild,
    restricted_role: discord.Role,
    verified_role: discord.Role,
    honeypot_channel_id: int | None = None,
) -> dict[str, int | bool]:
    """
    啟用驗證系統時執行的一次性批次操作：
    1. 把已驗證身分組發給目前所有現有成員（機器人排除）
    2. 把所有「原本 @everyone 就看得到、且非公告頻道、非蜜罐頻道」的文字頻道設定為 @everyone 無法發言，
       只有已驗證身分組能發言
       （本來就對 @everyone 隱藏的頻道、公告頻道、蜜罐頻道、以及所有語音頻道都會被略過，不受此操作影響；
       蜜罐頻道必須維持 @everyone 可發言，否則違規者無法在裡面留言觸發偵測，蜜罐功能會失效）

    Args:
        guild: 伺服器物件
        restricted_role: 待驗證身分組
        verified_role: 已驗證身分組
        honeypot_channel_id: 蜜罐頻道 ID；尚未設定蜜罐功能時為 None

    Returns:
        包含成功狀態、完成數量、失敗數量與回復失敗數量的結果
    """
    bot_member = guild.me
    lockable_channels = [
        channel for channel in guild.text_channels
        if not _is_hidden_from_everyone(channel)
        and not _is_announcement_channel(channel)
        and channel.id != honeypot_channel_id
    ]
    preflight_failed = (
        bot_member is None
        or restricted_role.guild.id != guild.id
        or verified_role.guild.id != guild.id
        or restricted_role == verified_role
        or restricted_role.is_default()
        or verified_role.is_default()
        or restricted_role.managed
        or verified_role.managed
        or not bot_member.guild_permissions.manage_roles
        or not bot_member.guild_permissions.manage_channels
        or bot_member.top_role <= restricted_role
        or bot_member.top_role <= verified_role
        or any(not channel.permissions_for(bot_member).manage_channels for channel in lockable_channels)
    )
    if preflight_failed:
        logger.error("驗證系統啟用前檢查失敗（伺服器 ID=%s）", guild.id)
        return {
            "success": False,
            "member_count": 0,
            "channel_count": 0,
            "failure_count": 1,
            "rollback_failure_count": 0,
        }

    granted_members: list[discord.Member] = []
    for member in guild.members:
        if member.bot or verified_role in member.roles:
            continue
        try:
            await member.add_roles(verified_role, reason="驗證系統啟用：既有成員自動放行")
            granted_members.append(member)
        except Exception as error:
            logger.error(f"授予既有成員已驗證身分組失敗（成員 ID={member.id}）：{error}", exc_info=True)
            rollback_failure_count = await _rollback_lockdown(guild, verified_role, granted_members, [])
            return {
                "success": False,
                "member_count": len(granted_members),
                "channel_count": 0,
                "failure_count": 1,
                "rollback_failure_count": rollback_failure_count,
            }
        await asyncio.sleep(LOCKDOWN_API_DELAY_SECONDS)

    channel_snapshots: list[
        tuple[discord.TextChannel, discord.PermissionOverwrite | None, discord.PermissionOverwrite | None]
    ] = []
    for channel in lockable_channels:
        everyone_snapshot = _copy_overwrite(channel.overwrites.get(guild.default_role))
        verified_snapshot = _copy_overwrite(channel.overwrites.get(verified_role))
        channel_snapshots.append((channel, everyone_snapshot, verified_snapshot))
        try:
            # 先讀出頻道原本的 overwrite 再修改單一欄位，避免覆蓋掉既有的 view_channel 等其他設定
            everyone_overwrite = channel.overwrites_for(guild.default_role)
            everyone_overwrite.send_messages = False
            await channel.set_permissions(guild.default_role, overwrite=everyone_overwrite)

            verified_overwrite = channel.overwrites_for(verified_role)
            verified_overwrite.send_messages = True
            await channel.set_permissions(verified_role, overwrite=verified_overwrite)

        except Exception as error:
            logger.error(f"鎖定頻道發言權限失敗（頻道 ID={channel.id}）：{error}", exc_info=True)
            rollback_failure_count = await _rollback_lockdown(
                guild, verified_role, granted_members, channel_snapshots
            )
            return {
                "success": False,
                "member_count": len(granted_members),
                "channel_count": len(channel_snapshots) - 1,
                "failure_count": 1,
                "rollback_failure_count": rollback_failure_count,
            }
        await asyncio.sleep(LOCKDOWN_API_DELAY_SECONDS)

    return {
        "success": True,
        "member_count": len(granted_members),
        "channel_count": len(channel_snapshots),
        "failure_count": 0,
        "rollback_failure_count": 0,
    }



