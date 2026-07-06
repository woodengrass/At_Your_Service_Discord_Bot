import datetime
import io
import logging

import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands

from core.i18n import i18n

logger = logging.getLogger(__name__)


class MessageTools(commands.Cog):
    """提供訊息刪除、公告與聊天紀錄匯出指令。"""

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
        """匯出目前頻道指定時間範圍內的聊天紀錄。"""
        await interaction.response.defer(ephemeral=True)
        cutoff_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
        content_list: list[str] = []
        exported_count = 0
        start_message = i18n.get_text("messages.export_start", interaction.guild.id, hours=hours)
        await interaction.followup.send(start_message, ephemeral=True)

        try:
            async for message in interaction.channel.history(limit=limit):
                if message.created_at < cutoff_time:
                    break
                timestamp = message.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                content = message.content or i18n.get_text("messages.export_attachment", interaction.guild.id)
                content_list.append(f"[{timestamp}] {message.author.display_name}: {content}")
                exported_count += 1
                if exported_count % 100 == 0:
                    progress_message = i18n.get_text(
                        "messages.export_progress", interaction.guild.id, count=exported_count
                    )
                    await interaction.edit_original_response(content=progress_message)

            if not content_list:
                no_message_text = i18n.get_text("messages.export_no_msg", interaction.guild.id, hours=hours)
                await interaction.edit_original_response(content=no_message_text)
                return

            content_list.reverse()
            file_data = io.BytesIO("\n".join(content_list).encode("utf-8"))
            discord_file = discord.File(file_data, filename=f"chat_log_{interaction.channel.name}.txt")
            public_message = i18n.get_text(
                "messages.export_done_public",
                interaction.guild.id,
                channel=interaction.channel.mention,
                count=len(content_list),
            )
            await interaction.channel.send(content=public_message, file=discord_file)
            done_message = i18n.get_text("messages.export_done_private", interaction.guild.id)
            await interaction.edit_original_response(content=done_message)
        except (discord.Forbidden, discord.HTTPException) as error:
            logger.error(f"匯出聊天紀錄失敗：{error}", exc_info=True)
            error_message = i18n.get_text("messages.error_unknown", interaction.guild.id)
            await interaction.edit_original_response(content=error_message)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MessageTools())
