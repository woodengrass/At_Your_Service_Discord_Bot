import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands

from core.i18n import i18n
from hubs.anti_fraud.panel import AntiFraudView


class AntiFraudHub(commands.Cog):
    """提供反詐騙功能集合的設定入口。"""

    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.command(name="anti_fraud_setting", description=locale_str("anti_fraud_setting"))
    async def anti_fraud_setting(self, interaction: discord.Interaction) -> None:
        """顯示反詐騙設定面板。"""
        view = AntiFraudView(interaction.guild.id)
        embed = discord.Embed(
            title=i18n.get_text("messages.anti_fraud_title", interaction.guild.id),
            description=i18n.get_text("messages.anti_fraud_desc", interaction.guild.id),
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AntiFraudHub())
