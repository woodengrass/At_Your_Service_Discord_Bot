import discord
from discord.ext import commands
import json, os

WELCOME_FILE = "config/welcome_message.json"

def load_welcome_config():
    #讀取歡迎訊息配置
    if not os.path.exists(WELCOME_FILE):
        return {}
    try:
        with open(WELCOME_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}

class WelcomeListener(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        #當新成員加入伺服器時觸發
        data = load_welcome_config()
        guild_id = str(member.guild.id)

        if guild_id not in data:
            return  # 該伺服器沒有設定歡迎訊息

        # 取得頻道與自訂訊息
        channel_id = int(data[guild_id]["channel_id"])
        message = data[guild_id]["message"]

        channel = member.guild.get_channel(channel_id)
        if not channel:
            return  # 設定的頻道不存在

        # 計算伺服器成員數（含機器人）
        count = len(member.guild.members)

        # 建立歡迎訊息
        embed = discord.Embed(
            title="🎉 歡迎加入！",
            description=f"歡迎 {member.mention} 加入 **{member.guild.name}**！\n"
                        f"{message}\n\n"
                        f"你是第 **{count}** 個加入的成員！",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=member.display_avatar.url)  # 顯示頭像

        # 發送文字 + Embed，確保標註顯示
        await channel.send(
            content=f"歡迎 {member.mention} 🎉",
            embed=embed
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(WelcomeListener(bot))