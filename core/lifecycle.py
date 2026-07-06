import asyncio
import logging

import discord
from discord.ext import commands, tasks

from features.custom_panels.cog import CustomPanelView
from features.tickets.cog import TicketControlView, TicketOpenButton
from features.custom_panels.repository import CustomPanelStore
from features.tickets.repository import TicketStore
from core.audit_log_repository import delete_guild_logs
from features.verification.repository import (
    delete_guild_entries, get_stale_review_channels, reset_flagged_entry_by_channel
)

logger = logging.getLogger(__name__)


class Lifecycle(commands.Cog):
    """
    負責在機器人啟動時註冊持久化 View，並定期清理已失效的面板與客服單紀錄。
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._views_added = False

    async def cog_load(self) -> None:
        """
        載入 Cog 時啟動清理背景任務。
        """
        self.cleanup_task.start()

    def cog_unload(self) -> None:
        """
        卸載 Cog 時停止清理背景任務。
        """
        self.cleanup_task.cancel()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """
        機器人被移出伺服器時，立即清除該伺服器的稽核紀錄，避免在沒有正當理由的情況下繼續保留資料。

        Args:
            guild: 被移出的伺服器物件
        """
        deleted_count = await delete_guild_logs(guild.id)
        if deleted_count:
            print(f"[資訊] 已移出伺服器 {guild.id}，清除 {deleted_count} 筆稽核紀錄。")

        deleted_verifications = await delete_guild_entries(guild.id)
        if deleted_verifications:
            print(f"[資訊] 已移出伺服器 {guild.id}，清除 {deleted_verifications} 筆驗證紀錄。")

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """
        機器人啟動時，只負責將 View 註冊到記憶體中 (add_view)。
        不進行 fetch_message，以避免 API Rate Limit 和啟動阻塞。
        """
        if self._views_added:
            return

        print("[資訊] Bot 已上線，正在註冊持久化視圖（Memory Only）...")

        # ===================================================
        # 1. 註冊 Custom Panels (自訂面板)
        # ===================================================
        panel_data = CustomPanelStore.data
        custom_panel_count = 0
        all_custom_panels = panel_data.get("panels", {})

        for message_id, panel_config in all_custom_panels.items():
            # 直接建立 View 物件並註冊，不需要去抓取 Discord 訊息
            try:
                view = CustomPanelView(self.bot, panel_config)
                self.bot.add_view(view)
                custom_panel_count += 1
            except Exception as e:
                logger.error(f"註冊 CustomPanel {message_id} 失敗：{e}", exc_info=True)

        # ===================================================
        # 2. 註冊 Ticket System (客服單)
        # ===================================================

        # A. 全域註冊 TicketControlView (關閉/刪除/紀錄按鈕)
        # 因為已經優化為無狀態 View，只需要註冊一次即可適用所有舊的客服單
        self.bot.add_view(TicketControlView())

        # B. 註冊 Ticket Panels (開單按鈕)
        ticket_data = TicketStore.data
        ticket_panel_count = 0

        for guild_id, guild_ticket_info in ticket_data.items():
            for panel in guild_ticket_info.get("panels", []):
                # 每個面板可能有不同的 reason，所以還是需要個別註冊
                try:
                    view = TicketOpenButton(self.bot, panel.get("reason", "開單"), int(guild_id))
                    self.bot.add_view(view)
                    ticket_panel_count += 1
                except Exception as e:
                    logger.error(f"註冊 TicketPanel 失敗：{e}", exc_info=True)

        print(f"[資訊] 視圖註冊完成：{custom_panel_count} 個自訂面板，{ticket_panel_count} 個開單面板。")
        self._views_added = True

    # ---------------------------------------------------
    # 背景任務：清理無效紀錄 (每 12 小時檢查一次)
    # ---------------------------------------------------
    @tasks.loop(hours=12)
    async def cleanup_task(self) -> None:
        """
        定期檢查並清除已失效的自訂面板與客服單紀錄（例如頻道或訊息已被刪除）。
        """
        await self.bot.wait_until_ready()
        print("[背景任務] 開始執行無效訊息清理任務...")

        # --- 1. 清理 Custom Panel ---
        panel_data = CustomPanelStore.data
        panel_changed = False
        all_custom_panels = panel_data.get("panels", {})

        for message_id_str in list(all_custom_panels.keys()):
            panel_config = all_custom_panels[message_id_str]
            channel_id = panel_config.get("channel_id")
            channel = self.bot.get_channel(channel_id)

            if not channel:
                panel_changed = await CustomPanelStore.remove_panel(int(message_id_str)) or panel_changed
                continue

            try:
                await channel.fetch_message(int(message_id_str))
            except discord.NotFound:
                panel_changed = await CustomPanelStore.remove_panel(int(message_id_str)) or panel_changed
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.warning(f"檢查 Custom Panel 訊息時發生網路問題，先保留：{e}")

        if panel_changed:
            print("[背景任務] 已清理無效的 Custom Panel 紀錄。")

        # --- 2. 清理 Ticket System ---
        ticket_data = TicketStore.data
        ticket_changed = False

        for guild_id, guild_ticket_info in list(ticket_data.items()):
            guild = self.bot.get_guild(int(guild_id))
            if not guild:
                ticket_changed = await TicketStore.remove_guild(int(guild_id)) or ticket_changed
                continue

            # 清理開單面板 (Panels)
            for panel in guild_ticket_info.get("panels", [])[:]:
                channel = self.bot.get_channel(panel["channel_id"])
                if not channel:
                    ticket_changed = await TicketStore.remove_panel(int(guild_id), panel["message_id"]) or ticket_changed
                    continue
                try:
                    await channel.fetch_message(panel["message_id"])
                except discord.NotFound:
                    ticket_changed = await TicketStore.remove_panel(int(guild_id), panel["message_id"]) or ticket_changed
                except Exception as e:
                    logger.warning(f"檢查 Ticket Panel 訊息時發生錯誤，先保留：{e}")
                await asyncio.sleep(0.1)

            # 清理已開啟的客服單 (Tickets)
            # 只要檢查頻道還在不在即可，訊息不見沒關係 (反正 View 是全域的)
            for ticket in guild_ticket_info.get("tickets", [])[:]:
                channel = self.bot.get_channel(ticket["channel_id"])
                if not channel:
                    # 頻道已刪除，視為結案移除
                    ticket_changed = await TicketStore.remove_ticket(int(guild_id), ticket["channel_id"]) or ticket_changed

        if ticket_changed:
            print("[背景任務] 已清理無效的 Ticket 紀錄。")

        # --- 3. 清理已失效的驗證審核頻道 ---
        # 保險機制：正常情況下頻道刪除時驗證功能的 on_guild_channel_delete
        # 就會即時同步，這裡是防止漏接事件（例如機器人當下離線）時的後備清理
        stale_review_count = 0
        for guild_id, review_channel_id in await get_stale_review_channels():
            if self.bot.get_channel(review_channel_id):
                continue
            if await reset_flagged_entry_by_channel(guild_id, review_channel_id):
                stale_review_count += 1

        if stale_review_count:
            print(f"[背景任務] 已重置 {stale_review_count} 筆指向失效審核頻道的驗證紀錄。")

        print("[背景任務] 清理任務結束。")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Lifecycle(bot))

