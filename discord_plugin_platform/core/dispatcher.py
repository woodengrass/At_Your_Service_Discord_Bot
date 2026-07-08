import datetime
import json
import logging
import time

import discord

from core import bot_registry, quota, repository, suspension
from core.capability_api import CAPABILITY_OWNERS
from sandbox.worker import execute_plugin_event

logger = logging.getLogger(__name__)

EXECUTION_TIMEOUT_SECONDS = 2  # 暫定值，待第二階段實測後校準，見 design.md 第 5.4 節


async def dispatch_event(
    guild_id: int,
    event_type: str,
    event_payload: dict,
    target_plugin_id: str | None = None,
) -> bool:
    """
    把 Discord 事件分派給該伺服器已安裝、有訂閱這個事件的外掛執行。

    流程：停權檢查 → 執行次數配額檢查 → 建立沙箱執行 → 驗證動作清單 →
    動作次數配額檢查 → 真正執行動作 → 記錄稽核紀錄。

    Args:
        guild_id: 伺服器 ID
        event_type: 事件名稱，對應附錄 A 定義的事件
        event_payload: 事件資料
        target_plugin_id: 若指定，只分派給該外掛；仍會保留停權、啟用狀態、事件訂閱與配額檢查

    Returns:
        True 表示至少一個目標外掛成功完成；若沒有目標外掛成功則回傳 False
    """
    installations = await repository.get_enabled_installations_for_guild(guild_id)
    has_successful_execution = False

    for installation in installations:
        plugin_id = installation["plugin_id"]

        if target_plugin_id is not None and plugin_id != target_plugin_id:
            continue

        if not _installation_handles_event(installation, event_type):
            continue

        if suspension.is_suspended(plugin_id):
            continue  # 已停權，完全不建立沙箱，不消耗任何執行資源

        if not await quota.check_and_consume_execution_quota(guild_id, plugin_id):
            await repository.log_execution(guild_id, plugin_id, event_type, "[]", 0, "quota_exceeded")
            continue

        source_code = await repository.get_plugin_source(plugin_id, installation["installed_version"])
        if source_code is None:
            await repository.log_execution(
                guild_id,
                plugin_id,
                event_type,
                "[]",
                0,
                "crashed",
                "找不到外掛原始碼",
            )
            continue

        granted_capabilities = set(json.loads(installation["granted_capabilities_json"]))
        started_at = time.monotonic()
        try:
            actions = await execute_plugin_event(
                guild_id=guild_id,
                plugin_id=plugin_id,
                source_code=source_code,
                event_type=event_type,
                event_payload=event_payload,
                granted_capabilities=granted_capabilities,
            )
        except Exception as error:
            execution_ms = int((time.monotonic() - started_at) * 1000)
            logger.error(f"外掛執行失敗（plugin_id={plugin_id}）：{error}", exc_info=True)
            await repository.log_execution(
                guild_id, plugin_id, event_type, "[]", execution_ms, "crashed", str(error)
            )
            continue

        execution_ms = int((time.monotonic() - started_at) * 1000)

        if not _validate_actions(installation, actions):
            await repository.log_execution(
                guild_id, plugin_id, event_type, "[]", execution_ms, "rejected_invalid_action"
            )
            continue

        if actions and not await quota.check_and_consume_action_quota(guild_id, plugin_id, len(actions)):
            await repository.log_execution(guild_id, plugin_id, event_type, "[]", execution_ms, "quota_exceeded")
            continue

        await _execute_actions(guild_id, actions)
        await repository.log_execution(
            guild_id, plugin_id, event_type, json.dumps(actions, ensure_ascii=False), execution_ms, "success"
        )
        has_successful_execution = True

    return has_successful_execution


def _validate_actions(installation: dict, actions: list[dict]) -> bool:
    """
    驗證沙箱回傳的動作清單是否都在這次安裝授權的能力範圍內。

    Args:
        installation: 安裝紀錄
        actions: 沙箱回傳的動作清單

    Returns:
        True 表示全部通過驗證
    """
    granted_capabilities = set(json.loads(installation["granted_capabilities_json"]))
    for action in actions:
        action_type = action.get("type")
        required_capability = CAPABILITY_OWNERS.get(action_type)
        if required_capability is None or required_capability not in granted_capabilities:
            return False
    return True


def _installation_handles_event(installation: dict, event_type: str) -> bool:
    """
    檢查安裝紀錄對應的 manifest 是否訂閱指定事件。

    Args:
        installation: 安裝紀錄，需包含 manifest_json
        event_type: 事件名稱

    Returns:
        True 表示該外掛訂閱此事件
    """
    try:
        manifest_data = json.loads(installation["manifest_json"])
    except (KeyError, json.JSONDecodeError) as error:
        logger.error(f"解析外掛 manifest 失敗：{error}", exc_info=True)
        return False
    return event_type in manifest_data.get("event_hooks", [])


async def _execute_actions(guild_id: int, actions: list[dict]) -> None:
    """
    依序真正執行動作清單裡的每個動作，呼叫真正的 Discord API。

    單一動作失敗（例如頻道/成員/訊息已經不存在）只記錄錯誤並繼續處理其餘動作，
    不會因為其中一項失敗就讓整批已通過驗證的動作全部中止。

    Args:
        guild_id: 伺服器 ID
        actions: 已通過驗證的動作清單
    """
    bot = bot_registry.get_bot()
    guild = bot.get_guild(guild_id)
    if guild is None:
        logger.error(f"執行動作時找不到伺服器（guild_id={guild_id}），機器人可能已被移出")
        return

    for action in actions:
        handler = _ACTION_HANDLERS.get(action["type"])
        if handler is None:
            logger.error(f"未知的動作類型，略過（guild_id={guild_id}，action={action}）")
            continue
        try:
            await handler(guild, action["params"])
        except Exception as error:
            logger.error(f"執行動作失敗（guild_id={guild_id}，action={action}）：{error}", exc_info=True)


def _build_embed(embed_params: dict | None) -> discord.Embed | None:
    """
    把能力函式傳來的 embed 參數轉成 discord.Embed，對應附錄 A.2.1 的欄位（標題/描述/顏色/圖片網址）。

    Args:
        embed_params: dict 或 None

    Returns:
        discord.Embed，embed_params 是 None 時回傳 None
    """
    if not embed_params:
        return None
    embed = discord.Embed(
        title=embed_params.get("title"),
        description=embed_params.get("description"),
        color=embed_params.get("color"),
    )
    image_url = embed_params.get("image_url")
    if image_url:
        embed.set_image(url=image_url)
    return embed


def _build_view(buttons_params: list | None) -> discord.ui.View | None:
    """
    把能力函式傳來的 buttons 參數轉成 discord.ui.View。

    Args:
        buttons_params: list of {"label": str, "custom_id": str, "style": str}，或 None

    Returns:
        discord.ui.View，buttons_params 是 None 或空清單時回傳 None
    """
    if not buttons_params:
        return None
    view = discord.ui.View(timeout=None)
    for button_params in buttons_params:
        style = getattr(discord.ButtonStyle, button_params.get("style", "primary"), discord.ButtonStyle.primary)
        view.add_item(
            discord.ui.Button(
                label=button_params.get("label", ""),
                style=style,
                custom_id=button_params.get("custom_id"),
            )
        )
    return view


async def _handle_send_message(guild: discord.Guild, params: dict) -> None:
    channel = guild.get_channel(params["channel_id"])
    if channel is None:
        return
    await channel.send(
        content=params.get("content"),
        embed=_build_embed(params.get("embed")),
        view=_build_view(params.get("buttons")),
    )


async def _handle_reply_message(guild: discord.Guild, params: dict) -> None:
    channel = guild.get_channel(params["channel_id"])
    if channel is None:
        return
    message = await channel.fetch_message(params["message_id"])
    await message.reply(
        content=params.get("content"),
        embed=_build_embed(params.get("embed")),
        view=_build_view(params.get("buttons")),
    )


async def _handle_edit_message(guild: discord.Guild, params: dict) -> None:
    channel = guild.get_channel(params["channel_id"])
    if channel is None:
        return
    message = await channel.fetch_message(params["message_id"])
    await message.edit(content=params.get("content"), embed=_build_embed(params.get("embed")))


async def _handle_pin_message(guild: discord.Guild, params: dict) -> None:
    channel = guild.get_channel(params["channel_id"])
    if channel is None:
        return
    message = await channel.fetch_message(params["message_id"])
    await message.pin()


async def _handle_unpin_message(guild: discord.Guild, params: dict) -> None:
    channel = guild.get_channel(params["channel_id"])
    if channel is None:
        return
    message = await channel.fetch_message(params["message_id"])
    await message.unpin()


async def _handle_send_poll(guild: discord.Guild, params: dict) -> None:
    channel = guild.get_channel(params["channel_id"])
    if channel is None:
        return
    poll = discord.Poll(question=params["question"], duration=datetime.timedelta(hours=params["duration"]))
    for option in params["options"]:
        poll.add_answer(text=option)
    await channel.send(poll=poll)


async def _handle_delete_message(guild: discord.Guild, params: dict) -> None:
    channel = guild.get_channel(params["channel_id"])
    if channel is None:
        return
    message = await channel.fetch_message(params["message_id"])
    await message.delete()


async def _handle_add_role(guild: discord.Guild, params: dict) -> None:
    member = guild.get_member(params["user_id"])
    role = guild.get_role(params["role_id"])
    if member is None or role is None:
        return
    await member.add_roles(role)


async def _handle_remove_role(guild: discord.Guild, params: dict) -> None:
    member = guild.get_member(params["user_id"])
    role = guild.get_role(params["role_id"])
    if member is None or role is None:
        return
    await member.remove_roles(role)


async def _handle_set_nickname(guild: discord.Guild, params: dict) -> None:
    member = guild.get_member(params["user_id"])
    if member is None:
        return
    await member.edit(nick=params["nickname"])


async def _handle_timeout_member(guild: discord.Guild, params: dict) -> None:
    member = guild.get_member(params["user_id"])
    if member is None:
        return
    until = discord.utils.utcnow() + datetime.timedelta(seconds=params["duration_seconds"])
    await member.timeout(until, reason=params.get("reason"))


async def _handle_create_thread(guild: discord.Guild, params: dict) -> None:
    channel = guild.get_channel(params["channel_id"])
    if channel is None:
        return
    await channel.create_thread(name=params["name"], type=discord.ChannelType.public_thread)


async def _handle_archive_thread(guild: discord.Guild, params: dict) -> None:
    thread = guild.get_thread(params["thread_id"])
    if thread is None:
        return
    await thread.edit(archived=True)


_ACTION_HANDLERS = {
    "send_message": _handle_send_message,
    "reply_message": _handle_reply_message,
    "edit_message": _handle_edit_message,
    "pin_message": _handle_pin_message,
    "unpin_message": _handle_unpin_message,
    "send_poll": _handle_send_poll,
    "delete_message": _handle_delete_message,
    "add_role": _handle_add_role,
    "remove_role": _handle_remove_role,
    "set_nickname": _handle_set_nickname,
    "timeout_member": _handle_timeout_member,
    "create_thread": _handle_create_thread,
    "archive_thread": _handle_archive_thread,
}
