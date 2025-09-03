import discord
from discord.ext import commands
import json
import os
from datetime import datetime

CONFIG_PATH = "config/honeypot_config.json"

def load_config():
    if not os.path.exists(CONFIG_PATH):
        return []
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def get_entry(config, guild_id):
    for entry in config:
        if entry["guild_id"] == str(guild_id):
            return entry
    return None

class DeleteListener(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        # 忽略 bot 自己
        if message.author.bot:
            return

        guild_id = str(message.guild.id)
        config = load_config()
        entry = get_entry(config, guild_id)

        if not entry:
            return

        # 是否啟用刪除記錄通報
        if not entry.get("enable_delete_log", False):
            return

        # 公告頻道 ID 是否存在
        announcement_channel_id = entry.get("announcement_channel")
        if not announcement_channel_id:
            return

        try:
            channel = message.guild.get_channel(int(announcement_channel_id))
            if not channel:
                return

            # 時間戳記（轉為台灣時區 GMT+8）
            timestamp = message.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")

            # 構建訊息內容
            content = message.content or "(訊息內容已空)"
            log_msg = (
                f"🗑️ 使用者 {message.author.mention} 在 <#{message.channel.id}> "
                f"於 `{timestamp}` 刪除了訊息：\n```\n{content[:1900]}\n```"
            )
            await channel.send(log_msg)

        except Exception as e:
            print(f"[刪除監聽錯誤] {e}")

async def setup(bot):
    await bot.add_cog(DeleteListener(bot))