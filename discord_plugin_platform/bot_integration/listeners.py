"""
掛在既有 discord.py bot 上的事件監聽器，轉發進 core/dispatcher.py。
第二階段開發重點（接上真正的 bot），見 design.md 第二階段。
"""

from discord.ext import commands

from core.dispatcher import dispatch_event


class PluginPlatformListeners(commands.Cog):
    """
    監聽 Discord 事件並轉發給外掛平台的 dispatcher。
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message) -> None:
        """
        轉發訊息事件給 dispatcher，交由已安裝且訂閱此事件的外掛處理。

        Args:
            message: Discord 訊息物件
        """
        if message.author.bot or not message.guild:
            return
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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PluginPlatformListeners(bot))
