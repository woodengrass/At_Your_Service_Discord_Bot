import discord
from discord.ext import commands
from discord.ui import View
import datetime
import os
import json

TICKET_FILE = "config/ticket.json"

def load_tickets():
    if not os.path.exists(TICKET_FILE):
        return {}
    try:
        with open(TICKET_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}

def save_tickets(data):
    os.makedirs("config", exist_ok=True)
    with open(TICKET_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def remove_ticket(guild_id: int, channel_id: int):
    ##刪除已關閉或刪除的客服單紀錄
    data = load_tickets()
    gid = str(guild_id)
    if gid not in data:
        return
    tickets = data[gid].get("tickets", [])
    new_list = [t for t in tickets if t["channel_id"] != channel_id]
    data[gid]["tickets"] = new_list
    save_tickets(data)
    print(f"[INFO] 已移除 {channel_id} 的客服單紀錄")

#客服單控制面板
class TicketControlView(View):
    def __init__(self, channel: discord.TextChannel, user: discord.User):
        super().__init__(timeout=None)
        self.channel = channel
        self.user = user

    @discord.ui.button(label="關閉客服單", style=discord.ButtonStyle.gray)
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.channel.set_permissions(self.user, send_messages=False)
        await interaction.response.send_message("🔒 已關閉客服單。", ephemeral=True)
        remove_ticket(interaction.guild.id, self.channel.id)

    @discord.ui.button(label="刪除客服單", style=discord.ButtonStyle.red)
    async def delete_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("⏳ 正在刪除客服單...", ephemeral=True)
        remove_ticket(interaction.guild.id, self.channel.id)
        await self.channel.delete()

    @discord.ui.button(label="輸出聊天紀錄", style=discord.ButtonStyle.blurple)
    async def export_transcript(self, interaction: discord.Interaction, button: discord.ui.Button):
        messages = [msg async for msg in self.channel.history(limit=None, oldest_first=True)]
        transcript = "\n".join([f"[{msg.created_at}] {msg.author}: {msg.content}" for msg in messages])

        filename = f"{self.channel.name}_transcript.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(transcript)

        await interaction.response.send_message("📄 已輸出聊天紀錄如下：", ephemeral=True)
        await interaction.followup.send(file=discord.File(filename))
        os.remove(filename)

#客服單開單按鈕
class TicketOpenButton(View):
    def __init__(self, bot: commands.Bot, label_text: str):
        super().__init__(timeout=None)
        self.bot = bot
        self.label_text = label_text

    @discord.ui.button(label="建立客服單", style=discord.ButtonStyle.green, custom_id="open_ticket")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        category = discord.utils.get(guild.categories, name="客服單")
        if category is None:
            category = await guild.create_category("客服單")

        channel_name = f"ticket-{interaction.user.name}".replace(" ", "-").lower()
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(view_channel=True),
        }
        admin_role = discord.utils.get(guild.roles, permissions=discord.Permissions(administrator=True))
        if admin_role:
            overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        ticket_channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)

        embed = discord.Embed(
            title="客服單已建立",
            description=f"這是 {interaction.user.mention} 開的單，開單理由為 `{self.label_text}`",
            color=discord.Color.green(),
            timestamp=datetime.datetime.utcnow()
        )
        ticket_message = await ticket_channel.send(embed=embed, view=TicketControlView(ticket_channel, interaction.user))
        await ticket_message.pin()#置頂

        # 寫入 ticket.json
        data = load_tickets()
        gid = str(guild.id)
        if gid not in data:
            data[gid] = {"tickets": [], "panels": []}

        data[gid]["tickets"].append({
            "message_id": ticket_message.id,
            "channel_id": ticket_channel.id,
            "user_id": interaction.user.id,
            "reason": self.label_text,
            "active": True
        })
        save_tickets(data)

        await interaction.response.send_message(f"✅ 已為你建立客服單：{ticket_channel.mention}", ephemeral=True)

class TicketSystem(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

async def setup(bot: commands.Bot):
    await bot.add_cog(TicketSystem(bot))
