import logging

import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands

from core.guild_settings import GuildSettings
from features.welcome.panel import WelcomeSettingView

logger = logging.getLogger(__name__)


class WelcomeListener(commands.Cog):
    """
    監聽新成員加入事件，並依伺服器設定發送客製化歡迎訊息。
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def format_text(self, text: str, member: discord.Member) -> str:
        """
        將文字中的自訂變數替換為實際內容。

        Args:
            text: 含有變數的原始文字，支援 [user]、[username]、[people]、[server]
            member: 觸發事件的成員物件

        Returns:
            替換完成後的文字
        """
        if not text:
            return ""
        return text.replace("[user]", member.mention) \
            .replace("[username]", member.display_name) \
            .replace("[people]", str(len(member.guild.members))) \
            .replace("[server]", member.guild.name)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """
        當新成員加入伺服器時觸發，發送歡迎 Embed 訊息。

        Args:
            member: 加入伺服器的成員物件
        """
        guild_id = member.guild.id
        welcome_config = GuildSettings.get_module_config(guild_id, "welcome")

        # 確認頻道已設定
        channel_id_str = welcome_config.get("channel_id")
        if not channel_id_str:
            return

        channel = member.guild.get_channel(int(channel_id_str))
        if not channel:
            return

        # 讀取設定檔 (兼容舊版設定檔)
        raw_title = welcome_config.get("title", "")
        raw_desc = welcome_config.get("desc", welcome_config.get("message", ""))
        raw_footer = welcome_config.get("footer", "")
        show_avatar = welcome_config.get("avatar", True)

        # 若內文為空則不發送
        if not raw_desc:
            return

        # 轉換自訂變數
        title = self.format_text(raw_title, member)
        desc = self.format_text(raw_desc, member)
        footer = self.format_text(raw_footer, member)

        # 構建 Embed
        embed = discord.Embed(description=desc, color=discord.Color.green())

        if title:
            embed.title = title

        if footer:
            embed.set_footer(text=footer)

        if show_avatar and member.display_avatar:
            embed.set_thumbnail(url=member.display_avatar.url)

        content_message = f"{member.mention}"

        try:
            await channel.send(content=content_message, embed=embed)
        except Exception as e:
            logger.error(f"發送歡迎訊息失敗：{e}", exc_info=True)


class WelcomeCommands(commands.Cog):
    """提供歡迎訊息設定入口。"""

    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.command(name="welcome_setting", description=locale_str("welcome_setting"))
    async def welcome_setting(self, interaction: discord.Interaction) -> None:
        """顯示歡迎訊息設定面板。"""
        view = WelcomeSettingView(interaction.guild.id)
        await interaction.response.send_message(embed=view.get_embed(), view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WelcomeListener(bot))
    await bot.add_cog(WelcomeCommands())

