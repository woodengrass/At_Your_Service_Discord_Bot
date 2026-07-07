import asyncio
import datetime
import logging
import os
import tempfile

import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands

from core.i18n import i18n

logger = logging.getLogger(__name__)

EXPORT_CHUNK_MAX_BYTES = 6_000_000
EXPORT_PROGRESS_INTERVAL = 1000


class MessageTools(commands.Cog):
    """提供訊息刪除、公告與聊天紀錄匯出指令。"""

    def __init__(self) -> None:
        self.export_lock = asyncio.Lock()

    async def _send_export_chunk(
        self,
        interaction: discord.Interaction,
        source_path: str,
        chunk_index: int,
    ) -> None:
        """
        上傳一個聊天紀錄分片，上傳完成後立即刪除本地檔案。

        Args:
            interaction: 發起匯出的互動物件
            source_path: 文字分片路徑
            chunk_index: 分片序號
        """
        filename = f"chat_log_{interaction.channel.name}_{chunk_index:04d}.txt"
        chunk_message = i18n.get_text(
            "messages.export_chunk", interaction.guild.id, index=chunk_index
        )
        discord_file = discord.File(source_path, filename=filename)
        try:
            await interaction.channel.send(
                content=chunk_message,
                file=discord_file,
            )
        finally:
            discord_file.close()
            try:
                if os.path.exists(source_path):
                    os.remove(source_path)
            except OSError as error:
                logger.error(f"刪除聊天匯出暫存檔失敗：{error}", exc_info=True)

    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.command(name="delete", description=locale_str("delete"))
    async def delete_messages(self, interaction: discord.Interaction, count: int) -> None:
        """刪除目前頻道最近的指定數量訊息。"""
        if not interaction.channel.permissions_for(interaction.user).manage_messages:
            message = i18n.get_text("messages.error_perm_manage_msg", interaction.guild.id)
            await interaction.response.send_message(message, ephemeral=True)
            return
        if count <= 0 or count > 100:
            message = i18n.get_text("messages.error_invalid_count", interaction.guild.id)
            await interaction.response.send_message(message, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            deleted_messages = await interaction.channel.purge(limit=count + 1)
        except discord.Forbidden:
            message = i18n.get_text("messages.delete_perm_error", interaction.guild.id)
            await interaction.followup.send(message)
            return
        except discord.HTTPException as error:
            message_key = "messages.delete_too_old_error" if error.code == 50034 else "messages.error_unknown"
            logger.error(f"批量刪除訊息失敗：{error}", exc_info=True)
            await interaction.followup.send(i18n.get_text(message_key, interaction.guild.id))
            return

        message = i18n.get_text(
            "messages.delete_success", interaction.guild.id, count=len(deleted_messages) - 1
        )
        await interaction.followup.send(message)

    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.command(name="announcement", description=locale_str("announcement"))
    async def announcement(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        content: str,
    ) -> None:
        """由機器人在指定頻道發送公告。"""
        try:
            await channel.send(content)
            message = i18n.get_text(
                "messages.announcement_sent", interaction.guild.id, channel=channel.mention
            )
            await interaction.response.send_message(message, ephemeral=True)
        except discord.Forbidden:
            message = i18n.get_text("messages.error_perm_send", interaction.guild.id)
            await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException as error:
            logger.error(f"發送公告失敗：{error}", exc_info=True)
            message = i18n.get_text("messages.error_unknown", interaction.guild.id)
            await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="export_chat", description=locale_str("export_chat"))
    async def export_chat(
        self,
        interaction: discord.Interaction,
        hours: int = 1,
        limit: int = 5000,
    ) -> None:
        """以固定記憶體與磁碟用量，串流匯出目前頻道指定範圍內的聊天紀錄。"""
        await interaction.response.defer(ephemeral=True)
        if hours < 0 or limit < 0:
            await interaction.edit_original_response(
                content=i18n.get_text("messages.export_invalid_range", interaction.guild.id)
            )
            return
        if self.export_lock.locked():
            await interaction.edit_original_response(
                content=i18n.get_text("messages.export_busy", interaction.guild.id)
            )
            return

        start_key = "messages.export_start_all" if hours == 0 else "messages.export_start"
        start_message = i18n.get_text(start_key, interaction.guild.id, hours=hours)
        await interaction.edit_original_response(content=start_message)

        async with self.export_lock:
            exported_count = 0
            chunk_index = 1
            cutoff_time = None
            if hours > 0:
                cutoff_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
            history_limit = None if limit == 0 else limit
            history_options = {"limit": history_limit, "oldest_first": True}
            if cutoff_time is not None:
                history_options["after"] = cutoff_time

            try:
                with tempfile.TemporaryDirectory(prefix="discord-chat-export-") as temp_directory:
                    source_path = os.path.join(temp_directory, f"chunk-{chunk_index:04d}.txt")
                    source_file = open(source_path, "wb")
                    chunk_bytes = 0
                    try:
                        async for history_message in interaction.channel.history(**history_options):
                            timestamp = history_message.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                            content = history_message.content or i18n.get_text(
                                "messages.export_attachment", interaction.guild.id
                            )
                            line = (
                                f"[{timestamp}] {history_message.author.display_name}: {content}\n"
                            ).encode("utf-8")
                            if chunk_bytes and chunk_bytes + len(line) > EXPORT_CHUNK_MAX_BYTES:
                                source_file.close()
                                await self._send_export_chunk(interaction, source_path, chunk_index)
                                chunk_index += 1
                                source_path = os.path.join(
                                    temp_directory, f"chunk-{chunk_index:04d}.txt"
                                )
                                source_file = open(source_path, "wb")
                                chunk_bytes = 0
                            source_file.write(line)
                            chunk_bytes += len(line)
                            exported_count += 1

                            if exported_count % EXPORT_PROGRESS_INTERVAL == 0:
                                progress_message = i18n.get_text(
                                    "messages.export_progress",
                                    interaction.guild.id,
                                    count=exported_count,
                                )
                                try:
                                    await interaction.edit_original_response(content=progress_message)
                                except discord.HTTPException as error:
                                    logger.warning("更新聊天匯出進度失敗：%s", error)
                    finally:
                        if not source_file.closed:
                            source_file.close()

                    if chunk_bytes:
                        await self._send_export_chunk(interaction, source_path, chunk_index)

                if exported_count == 0:
                    no_message_key = "messages.export_no_msg_all" if hours == 0 else "messages.export_no_msg"
                    no_message_text = i18n.get_text(
                        no_message_key, interaction.guild.id, hours=hours
                    )
                    await interaction.edit_original_response(content=no_message_text)
                    return

                done_message = i18n.get_text("messages.export_done_private", interaction.guild.id)
                try:
                    await interaction.edit_original_response(content=done_message)
                except discord.HTTPException as error:
                    logger.warning("更新聊天匯出完成狀態失敗：%s", error)
            except Exception as error:
                logger.error(f"匯出聊天紀錄失敗：{error}", exc_info=True)
                error_message = i18n.get_text("messages.error_unknown", interaction.guild.id)
                try:
                    await interaction.edit_original_response(content=error_message)
                except discord.HTTPException as response_error:
                    logger.error(f"回覆聊天匯出失敗訊息失敗：{response_error}", exc_info=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MessageTools())
