import datetime
import io  # 使用記憶體串流，不寫入硬碟
import logging

import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands
from discord.ui import View

from features.tickets.repository import TicketStore
from core.i18n import i18n

logger = logging.getLogger(__name__)

TRANSCRIPT_MESSAGE_LIMIT = 5000
TRANSCRIPT_FILE_MAX_BYTES = 7_500_000


async def remove_ticket(guild_id: int, channel_id: int) -> None:
    """
    從資料庫移除指定的客服單紀錄。

    Args:
        guild_id: 伺服器 ID
        channel_id: 客服單頻道 ID
    """
    await TicketStore.remove_ticket(guild_id, channel_id)


def get_ticket_owner_id(guild_id: int, channel_id: int) -> int | None:
    """
    取得指定客服單頻道的擁有者 ID。

    Args:
        guild_id: 伺服器 ID
        channel_id: 客服單頻道 ID

    Returns:
        擁有者的使用者 ID；若找不到對應紀錄則回傳 None
    """
    data = TicketStore.data
    guild_id_str = str(guild_id)
    if guild_id_str in data:
        for ticket in data[guild_id_str].get("tickets", []):
            if ticket["channel_id"] == channel_id:
                return ticket["user_id"]
    return None


class TicketControlView(View):
    """
    客服單控制面板，提供關閉、刪除頻道與匯出聊天紀錄三個按鈕。
    此 View 為無狀態設計，不需要傳入參數即可初始化，可套用於所有客服單。
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)

    # 1. 關閉客服單
    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary, custom_id="ticket_cmd:close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # 權限檢查
        if not interaction.user.guild_permissions.administrator:
            admin_only_message = i18n.get_text("messages.ticket_admin_only_close", interaction.guild.id)
            await interaction.response.send_message(admin_only_message, ephemeral=True)
            return

        await interaction.response.defer()

        # 嘗試鎖定擁有者的權限
        owner_id = get_ticket_owner_id(interaction.guild.id, interaction.channel.id)
        if owner_id:
            owner = interaction.guild.get_member(owner_id)
            if owner:
                try:
                    await interaction.channel.set_permissions(owner, send_messages=False)
                except discord.NotFound:
                    logger.info("客服單擁有者已離開伺服器，略過鎖定權限。")

        # 從資料庫移除 (視為已結案)
        await remove_ticket(interaction.guild.id, interaction.channel.id)

        closed_message = i18n.get_text("messages.ticket_closed", interaction.guild.id)
        await interaction.followup.send(closed_message)

        # 停用按鈕避免重複操作
        self.children[0].disabled = True  # Close
        await interaction.message.edit(view=self)

    # 2. 刪除頻道
    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, custom_id="ticket_cmd:delete")
    async def delete_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.user.guild_permissions.administrator:
            admin_only_message = i18n.get_text("messages.ticket_admin_only_delete", interaction.guild.id)
            await interaction.response.send_message(admin_only_message, ephemeral=True)
            return

        deleting_message = i18n.get_text("messages.ticket_deleting", interaction.guild.id)
        await interaction.response.send_message(deleting_message, ephemeral=True)

        await remove_ticket(interaction.guild.id, interaction.channel.id)

        try:
            await interaction.channel.delete()
        except discord.NotFound:
            logger.info("客服單頻道已不存在，略過刪除。")

    # 3. 匯出紀錄 (Transcript)
    @discord.ui.button(label="Transcript", style=discord.ButtonStyle.primary,
                       custom_id="ticket_cmd:transcript")
    async def export_transcript(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """
        匯出有限範圍的客服單聊天紀錄，並依 Discord 上傳大小分割檔案。

        Args:
            interaction: 觸發匯出的互動物件
            button: 被點擊的按鈕
        """
        owner_id = get_ticket_owner_id(interaction.guild.id, interaction.channel.id)
        is_allowed = interaction.user.guild_permissions.administrator or interaction.user.id == owner_id
        if not is_allowed:
            await interaction.response.send_message(
                i18n.get_text("messages.ticket_transcript_permission_denied", interaction.guild.id), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        file_buffers: list[io.BytesIO] = []
        transcript_lines: list[bytes] = []
        exported_count = 0
        try:
            async for message in interaction.channel.history(limit=TRANSCRIPT_MESSAGE_LIMIT):
                if not message.content:
                    continue
                timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
                line = f"[{timestamp}] {message.author.name}: {message.content}\n".encode("utf-8")
                transcript_lines.append(line)
                exported_count += 1
                if exported_count % 500 == 0:
                    await interaction.edit_original_response(
                        content=i18n.get_text(
                            "messages.export_progress", interaction.guild.id, count=exported_count
                        )
                    )

            if not transcript_lines:
                await interaction.edit_original_response(
                    content=i18n.get_text("messages.ticket_transcript_empty", interaction.guild.id)
                )
                return

            current_buffer = io.BytesIO()
            for line in reversed(transcript_lines):
                if current_buffer.tell() and current_buffer.tell() + len(line) > TRANSCRIPT_FILE_MAX_BYTES:
                    current_buffer.seek(0)
                    file_buffers.append(current_buffer)
                    current_buffer = io.BytesIO()
                current_buffer.write(line)
            current_buffer.seek(0)
            file_buffers.append(current_buffer)

            files = [
                discord.File(
                    file_buffer,
                    filename=f"transcript-{interaction.channel.name}-{index}.txt",
                )
                for index, file_buffer in enumerate(file_buffers, start=1)
            ]
            transcript_message = i18n.get_text("messages.ticket_transcript", interaction.guild.id)
            await interaction.followup.send(content=transcript_message, files=files, ephemeral=True)
            await interaction.edit_original_response(content=transcript_message)
        except Exception as error:
            logger.error(f"匯出客服單聊天紀錄失敗：{error}", exc_info=True)
            await interaction.edit_original_response(
                content=i18n.get_text("messages.ticket_transcript_failed", interaction.guild.id)
            )


class TicketOpenButton(View):
    """
    開單按鈕 View，需保留 reason 等參數，因此每個面板皆需個別建立實例。
    """

    def __init__(self, bot: commands.Bot, label_text: str, guild_id: int | None = None) -> None:
        super().__init__(timeout=None)
        self.label_text = label_text

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.success, custom_id="open_ticket")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild

        category = discord.utils.get(guild.categories, name="客服單")
        if category is None:
            category = await guild.create_category("客服單")

        # 避免同一個人開太多單，這裡可以加邏輯 (選用)

        channel_name = f"ticket-{interaction.user.name}".replace(" ", "-").lower()

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(view_channel=True),
        }

        try:
            ticket_channel = await guild.create_text_channel(
                channel_name,
                category=category,
                overwrites=overwrites
            )
        except Exception as e:
            logger.error(f"建立客服單頻道失敗：{e}", exc_info=True)
            create_failed_message = i18n.get_text("messages.ticket_channel_create_failed", guild.id)
            await interaction.response.send_message(create_failed_message, ephemeral=True)
            return

        title = i18n.get_text("messages.ticket_created_title", guild.id)
        description = i18n.get_text(
            "messages.ticket_created_desc",
            guild.id,
            user=interaction.user.mention,
            reason=self.label_text
        )

        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.green(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )

        # 使用無狀態的控制面板 View
        ticket_message = await ticket_channel.send(
            embed=embed,
            view=TicketControlView()
        )
        await ticket_message.pin()

        # 寫入資料庫
        await TicketStore.add_ticket(
            guild.id,
            {
                "message_id": ticket_message.id,
                "channel_id": ticket_channel.id,
                "user_id": interaction.user.id,
                "reason": self.label_text,
                "active": True,
            },
        )

        created_message = i18n.get_text(
            "messages.ticket_created_msg",
            guild.id,
            channel=ticket_channel.mention
        )
        await interaction.response.send_message(created_message, ephemeral=True)


class TicketSystem(commands.Cog):
    """
    監聽頻道刪除與訊息刪除事件，自動清理已失效的客服單與面板紀錄。
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        """
        當頻道被刪除時，若為客服單頻道則同步移除資料庫紀錄。

        Args:
            channel: 被刪除的頻道物件
        """
        if isinstance(channel, discord.TextChannel):
            await remove_ticket(channel.guild.id, channel.id)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        """
        當訊息被刪除時，若為客服單開單面板訊息則同步移除資料庫紀錄。

        Args:
            payload: 原始訊息刪除事件資料
        """
        if not payload.guild_id:
            return

        if await TicketStore.remove_panel(payload.guild_id, payload.message_id):
            print(f"[資訊] 偵測到 Ticket 面板訊息刪除，已清理紀錄（訊息 ID：{payload.message_id}）")


class TicketCommands(commands.Cog):
    """提供客服單面板建立指令。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.command(name="ticket", description=locale_str("ticket"))
    async def ticket(
        self,
        interaction: discord.Interaction,
        description: str,
        reason: str,
    ) -> None:
        """在目前頻道建立客服單入口面板。"""
        view = TicketOpenButton(self.bot, reason, interaction.guild.id)
        embed = discord.Embed(
            title=i18n.get_text("messages.ticket_embed_title", interaction.guild.id),
            description=description,
            color=discord.Color.blue(),
        )
        panel_created_message = i18n.get_text("messages.ticket_panel_created", interaction.guild.id)
        await interaction.response.send_message(panel_created_message, ephemeral=True)
        panel_message = await interaction.channel.send(embed=embed, view=view)
        await TicketStore.add_panel(
            interaction.guild.id,
            {
                "channel_id": interaction.channel.id,
                "message_id": panel_message.id,
                "reason": reason,
            },
        )
        log_message = i18n.get_text(
            "messages.ticket_created_log", interaction.guild.id, channel=interaction.channel.id
        )
        print(log_message)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TicketSystem(bot))
    await bot.add_cog(TicketCommands(bot))

