import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands

from core.i18n import i18n
from hubs.server_setting.panel import ServerSettingView


class ServerSettingHub(commands.Cog):
    """提供一般伺服器設定與語言設定入口。"""

    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.command(name="server_setting", description=locale_str("server_setting"))
    async def server_setting(self, interaction: discord.Interaction) -> None:
        """顯示伺服器設定面板。"""
        view = ServerSettingView(interaction.guild.id)
        embed = discord.Embed(
            title=i18n.get_text("messages.server_setting_title", interaction.guild.id),
            description=i18n.get_text("messages.server_setting_desc", interaction.guild.id),
            color=discord.Color.dark_gray(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.command(name="set_language", description=locale_str("set_language"))
    @app_commands.choices(
        lang=[
            app_commands.Choice(name=locale_str("labels.language_zh_tw"), value="zh-TW"),
            app_commands.Choice(name=locale_str("labels.language_zh_cn"), value="zh-CN"),
            app_commands.Choice(name=locale_str("labels.language_en_us"), value="en-US"),
        ]
    )
    async def set_language(
        self,
        interaction: discord.Interaction,
        lang: app_commands.Choice[str],
    ) -> None:
        """設定目前伺服器使用的介面語言。"""
        await i18n.set_lang(interaction.guild.id, lang.value)
        message = i18n.get_text("messages.lang_set", interaction.guild.id)
        await interaction.response.send_message(message, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerSettingHub())
