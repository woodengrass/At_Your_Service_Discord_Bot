import logging
import time
import uuid

import discord

from features.warnings.repository import WarningStore
from core.i18n import i18n
from core.ui_constants import MAX_SELECT_OPTIONS, PANEL_TIMEOUT_SECONDS, WARNING_PAGE_SIZE

# 全域字典，暫存使用者正在編輯的表單狀態
WIP_WARNINGS: dict[int, dict] = {}
WIP_WARNING_TIMEOUT_SECONDS = 1800  # 超過 30 分鐘未完成設定精靈則視為放棄
logger = logging.getLogger(__name__)


def cleanup_stale_wip_warnings() -> None:
    """
    清除超過逾時時間仍未完成設定精靈的暫存資料，避免使用者中途放棄造成記憶體洩漏。
    """
    now = time.time()
    expired_user_ids = [
        user_id for user_id, wip_data in WIP_WARNINGS.items()
        if now - wip_data.get("_created_at", now) > WIP_WARNING_TIMEOUT_SECONDS
    ]
    for user_id in expired_user_ids:
        del WIP_WARNINGS[user_id]


def get_warnings(guild_id: int) -> dict:
    """
    取得指定伺服器的所有定時提醒排程。

    Args:
        guild_id: 伺服器 ID

    Returns:
        dict，鍵為提醒 ID，值為該筆提醒的設定資料
    """
    # 過濾出當前伺服器的設定
    return {key: value for key, value in WarningStore.data.items() if value.get("guild_id") == guild_id}


# ==============================================================================
#  精靈步驟 3：排程時間設定 (Schedule)
# ==============================================================================
class WarningTimeModal(discord.ui.Modal):
    """
    設定提醒排程時間（與每週/每月日期）的表單。
    """

    def __init__(self, guild_id: int, frequency_type: str, user_id: int, parent_view: "WarningSettingView") -> None:
        super().__init__(title=i18n.get_text("ui.modal_time_title", guild_id)[:45])
        self.guild_id = guild_id
        self.frequency_type = frequency_type
        self.user_id = user_id
        self.parent_view = parent_view  # 最原始的 WarningSettingView

        # 預設值讀取 (如果是編輯模式)
        wip_data = WIP_WARNINGS.get(user_id, {})
        schedule_config = wip_data.get("schedule", {})

        self.time_input = discord.ui.TextInput(
            label=i18n.get_text("ui.input_time_hhmm", guild_id)[:45],
            placeholder=i18n.get_text("ui.placeholder_time_hhmm", guild_id),
            default=schedule_config.get("time", "12:00"),
            required=True, max_length=5
        )
        self.add_item(self.time_input)

        # 只有每週或每月才需要輸入日期
        if frequency_type in ["weekly", "monthly"]:
            default_days = ",".join(map(str, schedule_config.get("days", [])))
            self.days_input = discord.ui.TextInput(
                label=i18n.get_text("ui.input_time_days", guild_id)[:45],
                placeholder=i18n.get_text("ui.placeholder_time_days", guild_id),
                default=default_days,
                required=True, max_length=50
            )
            self.add_item(self.days_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        wip_data = WIP_WARNINGS.get(self.user_id)
        if not wip_data:
            error_message = i18n.get_text("messages.error_wip_not_found", self.guild_id)
            await interaction.response.send_message(error_message, ephemeral=True)
            return

        # 儲存排程
        days = []
        if self.frequency_type in ["weekly", "monthly"]:
            try:
                days = [int(day_str.strip()) for day_str in self.days_input.value.split(",") if day_str.strip().isdigit()]
            except ValueError:
                logger.exception("Failed to parse warning schedule days for guild %s", self.guild_id)
                error_message = i18n.get_text("messages.error_invalid_days_format", self.guild_id)
                await interaction.response.send_message(error_message, ephemeral=True)
                return

        wip_data["schedule"] = {
            "type": self.frequency_type,
            "time": self.time_input.value.strip(),
            "days": days
        }

        # 寫入資料庫
        warning_id = wip_data.get("id", f"warn_{uuid.uuid4().hex[:8]}")

        await WarningStore.set_warning(
            warning_id,
            {
                "guild_id": self.guild_id,
                "channel_id": wip_data.get("channel_id"),
                "role_id": wip_data.get("role_id"),
                "active": wip_data.get("active", True),
                "schedule": wip_data["schedule"],
                "content": wip_data["content"],
            },
        )

        # 清除暫存
        if self.user_id in WIP_WARNINGS:
            del WIP_WARNINGS[self.user_id]

        # 刷新主面板
        updated_view = WarningSettingView(self.guild_id, self.parent_view.page)
        await interaction.response.edit_message(content=None, embed=updated_view.get_embed(), view=updated_view)
        await interaction.followup.send(i18n.get_text("messages.warning_success_saved", self.guild_id), ephemeral=True)


class WarningScheduleView(discord.ui.View):
    """
    排程頻率選擇視圖（每天/每週/每月）。
    """

    def __init__(self, guild_id: int, user_id: int, parent_view: "WarningSettingView") -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.guild_id = guild_id
        self.user_id = user_id
        self.parent_view = parent_view

        options = [
            discord.SelectOption(label=i18n.get_text("ui.opt_freq_daily", guild_id), value="daily"),
            discord.SelectOption(label=i18n.get_text("ui.opt_freq_weekly", guild_id), value="weekly"),
            discord.SelectOption(label=i18n.get_text("ui.opt_freq_monthly", guild_id), value="monthly"),
        ]
        frequency_select = discord.ui.Select(placeholder=i18n.get_text("ui.placeholder_freq_select", guild_id), options=options)
        frequency_select.callback = self.frequency_callback
        self.add_item(frequency_select)
        back_button = discord.ui.Button(
            label=i18n.get_text("ui.btn_back", guild_id), style=discord.ButtonStyle.secondary
        )
        back_button.callback = self.back_callback
        self.add_item(back_button)
        cancel_button = discord.ui.Button(
            label=i18n.get_text("ui.btn_cancel", guild_id), style=discord.ButtonStyle.secondary
        )
        cancel_button.callback = self.cancel_callback
        self.add_item(cancel_button)

    async def frequency_callback(self, interaction: discord.Interaction) -> None:
        frequency_type = interaction.data["values"][0]
        await interaction.response.send_modal(
            WarningTimeModal(self.guild_id, frequency_type, self.user_id, self.parent_view))

    async def back_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            content=i18n.get_text("ui.msg_warning_target", self.guild_id),
            embed=None,
            view=WarningTargetView(self.guild_id, self.user_id, self.parent_view),
        )

    async def cancel_callback(self, interaction: discord.Interaction) -> None:
        WIP_WARNINGS.pop(self.user_id, None)
        await interaction.response.edit_message(
            content=None, embed=self.parent_view.get_embed(), view=self.parent_view
        )


# ==============================================================================
#  精靈步驟 2：目標設定 (Targets)
# ==============================================================================
class WarningTargetView(discord.ui.View):
    """
    設定提醒發送頻道與標註身分組的視圖。
    """

    def __init__(self, guild_id: int, user_id: int, parent_view: "WarningSettingView") -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.guild_id = guild_id
        self.user_id = user_id
        self.parent_view = parent_view

        # 頻道選擇
        channel_select = discord.ui.ChannelSelect(
            placeholder=i18n.get_text("ui.placeholder_warning_channel", guild_id),
            channel_types=[discord.ChannelType.text],
            min_values=1, max_values=1
        )
        channel_select.callback = self.channel_callback
        self.add_item(channel_select)

        # 身分組選擇
        role_select = discord.ui.RoleSelect(
            placeholder=i18n.get_text("ui.placeholder_warning_role", guild_id), min_values=0, max_values=1
        )
        role_select.callback = self.role_callback
        self.add_item(role_select)

        # 下一步按鈕
        next_button = discord.ui.Button(label=i18n.get_text("ui.btn_next_step", guild_id),
                                        style=discord.ButtonStyle.primary)
        next_button.callback = self.next_callback
        self.add_item(next_button)
        back_button = discord.ui.Button(
            label=i18n.get_text("ui.btn_back", guild_id), style=discord.ButtonStyle.secondary
        )
        back_button.callback = self.back_callback
        self.add_item(back_button)
        cancel_button = discord.ui.Button(
            label=i18n.get_text("ui.btn_cancel", guild_id), style=discord.ButtonStyle.secondary
        )
        cancel_button.callback = self.cancel_callback
        self.add_item(cancel_button)

    async def channel_callback(self, interaction: discord.Interaction) -> None:
        if self.user_id in WIP_WARNINGS:
            WIP_WARNINGS[self.user_id]["channel_id"] = interaction.data["values"][0]
        await interaction.response.defer()

    async def role_callback(self, interaction: discord.Interaction) -> None:
        if self.user_id in WIP_WARNINGS and interaction.data["values"]:
            WIP_WARNINGS[self.user_id]["role_id"] = interaction.data["values"][0]
        await interaction.response.defer()

    async def next_callback(self, interaction: discord.Interaction) -> None:
        wip_data = WIP_WARNINGS.get(self.user_id, {})
        if "channel_id" not in wip_data or not wip_data["channel_id"]:
            error_message = i18n.get_text("messages.error_channel_required", self.guild_id)
            await interaction.response.send_message(error_message, ephemeral=True)
            return

        view = WarningScheduleView(self.guild_id, self.user_id, self.parent_view)
        schedule_prompt = i18n.get_text("ui.msg_warning_schedule", self.guild_id)
        await interaction.response.edit_message(content=schedule_prompt, view=view)

    async def back_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            WarningContentModal(
                self.guild_id,
                self.user_id,
                self.parent_view,
                preserve_existing=True,
            )
        )

    async def cancel_callback(self, interaction: discord.Interaction) -> None:
        WIP_WARNINGS.pop(self.user_id, None)
        await interaction.response.edit_message(
            content=None, embed=self.parent_view.get_embed(), view=self.parent_view
        )


# ==============================================================================
#  精靈步驟 1：內容設定表單 (Content Modal)
# ==============================================================================
class WarningContentModal(discord.ui.Modal):
    """
    設定提醒內容（標題、內文、底部、縮圖與大圖）的表單。
    """

    def __init__(
        self,
        guild_id: int,
        user_id: int,
        parent_view: "WarningSettingView",
        edit_id: str | None = None,
        preserve_existing: bool = False,
    ) -> None:
        super().__init__(title=i18n.get_text("ui.modal_warning_content_title", guild_id)[:45])
        self.guild_id = guild_id
        self.user_id = user_id
        self.parent_view = parent_view

        # 初始化暫存
        if not preserve_existing:
            WIP_WARNINGS[user_id] = {}
            if edit_id:
                WIP_WARNINGS[user_id] = dict(WarningStore.data.get(edit_id, {}))
                WIP_WARNINGS[user_id]["id"] = edit_id
        WIP_WARNINGS[user_id]["_created_at"] = time.time()

        wip_content = WIP_WARNINGS[user_id].get("content", {})

        self.title_input = discord.ui.TextInput(
            label=i18n.get_text("ui.input_warning_title", guild_id)[:45],
            default=wip_content.get("title", ""), required=False, max_length=256
        )
        self.add_item(self.title_input)

        self.desc_input = discord.ui.TextInput(
            label=i18n.get_text("ui.input_warning_desc", guild_id)[:45], style=discord.TextStyle.paragraph,
            default=wip_content.get("desc", ""), required=True, max_length=2000
        )
        self.add_item(self.desc_input)

        self.footer_input = discord.ui.TextInput(
            label=i18n.get_text("ui.input_warning_footer", guild_id)[:45],
            default=wip_content.get("footer", ""), required=False, max_length=1024
        )
        self.add_item(self.footer_input)

        self.thumbnail_input = discord.ui.TextInput(
            label=i18n.get_text("ui.input_warning_thumb", guild_id)[:45],
            default=wip_content.get("thumbnail", ""), required=False
        )
        self.add_item(self.thumbnail_input)

        self.image_input = discord.ui.TextInput(
            label=i18n.get_text("ui.input_warning_image", guild_id)[:45],
            default=wip_content.get("image", ""), required=False
        )
        self.add_item(self.image_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if self.user_id in WIP_WARNINGS:
            WIP_WARNINGS[self.user_id]["content"] = {
                "title": self.title_input.value.strip(),
                "desc": self.desc_input.value.strip(),
                "footer": self.footer_input.value.strip(),
                "thumbnail": self.thumbnail_input.value.strip(),
                "image": self.image_input.value.strip()
            }

        view = WarningTargetView(self.guild_id, self.user_id, self.parent_view)
        target_prompt = i18n.get_text("ui.msg_warning_target", self.guild_id)
        await interaction.response.edit_message(content=target_prompt, embed=None, view=view)


# ==============================================================================
#  操作選擇器 (Action Selects)
# ==============================================================================
class WarningListSelect(discord.ui.Select):
    """用於列出單頁提醒供編輯、刪除或切換狀態。"""

    def __init__(self, guild_id: int, action: str, parent_view: "WarningSettingView", page: int) -> None:
        self.guild_id = guild_id
        self.action = action
        self.parent_view = parent_view

        warning_items = list(get_warnings(guild_id).items())
        page_start = page * MAX_SELECT_OPTIONS
        page_items = warning_items[page_start:page_start + MAX_SELECT_OPTIONS]
        options = []
        for warning_id, warning_data in page_items:
            title = warning_data.get("content", {}).get("title", i18n.get_text("labels.untitled", guild_id))[:50]
            status_text = i18n.get_text(
                "labels.warning_active" if warning_data.get("active", True) else "labels.warning_paused", guild_id
            )
            identifier_text = i18n.get_text("labels.identifier", guild_id, identifier=warning_id)
            options.append(
                discord.SelectOption(label=f"{title} ({status_text})"[:100], value=warning_id, description=identifier_text)
            )

        super().__init__(placeholder=i18n.get_text("ui.placeholder_select_warning", guild_id), options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        warning_id = self.values[0]
        if self.action == "edit":
            await interaction.response.send_modal(
                WarningContentModal(self.guild_id, interaction.user.id, self.parent_view, warning_id))

        elif self.action == "delete":
            await WarningStore.remove_warning(warning_id)
            updated_view = WarningSettingView(self.guild_id, self.parent_view.page)
            await interaction.response.edit_message(content=None, embed=updated_view.get_embed(), view=updated_view)
            await interaction.followup.send(i18n.get_text("messages.warning_success_deleted", self.guild_id),
                                            ephemeral=True)

        elif self.action == "toggle":
            active = await WarningStore.toggle_warning(warning_id)
            if active is None:
                return
            status_text = i18n.get_text(
                "labels.warning_active" if active else "labels.warning_paused", self.guild_id
            )
            toggled_message = i18n.get_text("messages.warning_success_toggled", self.guild_id, status=status_text)

            updated_view = WarningSettingView(self.guild_id, self.parent_view.page)
            await interaction.response.edit_message(content=None, embed=updated_view.get_embed(), view=updated_view)
            await interaction.followup.send(toggled_message, ephemeral=True)


class WarningSelectionView(discord.ui.View):
    """
    顯示提醒操作目標的分頁選單。
    """

    def __init__(self, guild_id: int, action: str, parent_view: "WarningSettingView", page: int = 0) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.guild_id = guild_id
        self.action = action
        self.parent_view = parent_view
        warning_count = len(get_warnings(guild_id))
        self.total_pages = max(1, (warning_count + MAX_SELECT_OPTIONS - 1) // MAX_SELECT_OPTIONS)
        self.page = min(max(page, 0), self.total_pages - 1)
        self.add_item(WarningListSelect(guild_id, action, parent_view, self.page))

        if self.page > 0:
            previous_button = discord.ui.Button(
                label=i18n.get_text("ui.btn_previous_page", guild_id), style=discord.ButtonStyle.secondary
            )
            previous_button.callback = self.previous_page
            self.add_item(previous_button)
        if self.page < self.total_pages - 1:
            next_button = discord.ui.Button(
                label=i18n.get_text("ui.btn_next_page", guild_id), style=discord.ButtonStyle.secondary
            )
            next_button.callback = self.next_page
            self.add_item(next_button)

        back_button = discord.ui.Button(
            label=i18n.get_text("ui.btn_back", guild_id), style=discord.ButtonStyle.secondary
        )
        back_button.callback = self.back_to_main
        self.add_item(back_button)

    async def previous_page(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            view=WarningSelectionView(self.guild_id, self.action, self.parent_view, self.page - 1)
        )

    async def next_page(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            view=WarningSelectionView(self.guild_id, self.action, self.parent_view, self.page + 1)
        )

    async def back_to_main(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            content=None, embed=self.parent_view.get_embed(), view=self.parent_view
        )


class WarningActionSelect(discord.ui.Select):
    """主面板的一級菜單"""

    def __init__(self, guild_id: int, parent_view: "WarningSettingView") -> None:
        self.guild_id = guild_id
        self.parent_view = parent_view

        options = [
            discord.SelectOption(label=i18n.get_text("ui.opt_add_warning", guild_id), value="add"),
            discord.SelectOption(label=i18n.get_text("ui.opt_edit_warning", guild_id), value="edit"),
            discord.SelectOption(label=i18n.get_text("ui.opt_toggle_warning", guild_id), value="toggle"),
            discord.SelectOption(label=i18n.get_text("ui.opt_delete_warning", guild_id), value="delete"),
        ]
        super().__init__(placeholder=i18n.get_text("ui.placeholder_warning_action", guild_id), options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        selected_value = self.values[0]

        if selected_value == "add":
            await interaction.response.send_modal(
                WarningContentModal(self.guild_id, interaction.user.id, self.parent_view))
        else:
            # Edit, Toggle, Delete 必須要有現存資料才能操作
            warnings = get_warnings(self.guild_id)
            if not warnings:
                error_message = i18n.get_text("messages.error_no_warnings", self.guild_id)
                await interaction.response.send_message(error_message, ephemeral=True)
                return

            view = WarningSelectionView(self.guild_id, selected_value, self.parent_view)
            target_prompt = i18n.get_text("messages.select_target_prompt", self.guild_id)
            await interaction.response.edit_message(content=target_prompt, embed=None, view=view)


# ==============================================================================
#  主面板視圖 (Main View)
# ==============================================================================
class WarningSettingView(discord.ui.View):
    """
    定時提醒設定的主面板視圖。
    """

    def __init__(self, guild_id: int, page: int = 0) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.guild_id = guild_id
        warning_count = len(get_warnings(guild_id))
        self.total_pages = max(1, (warning_count + WARNING_PAGE_SIZE - 1) // WARNING_PAGE_SIZE)
        self.page = min(max(page, 0), self.total_pages - 1)
        self._build_items()

    def _build_items(self) -> None:
        self.clear_items()
        self.add_item(WarningActionSelect(self.guild_id, self))
        if self.page > 0:
            previous_button = discord.ui.Button(
                label=i18n.get_text("ui.btn_previous_page", self.guild_id),
                style=discord.ButtonStyle.secondary,
            )
            previous_button.callback = self.previous_page
            self.add_item(previous_button)
        if self.page < self.total_pages - 1:
            next_button = discord.ui.Button(
                label=i18n.get_text("ui.btn_next_page", self.guild_id),
                style=discord.ButtonStyle.secondary,
            )
            next_button.callback = self.next_page
            self.add_item(next_button)

    async def previous_page(self, interaction: discord.Interaction) -> None:
        view = WarningSettingView(self.guild_id, self.page - 1)
        await interaction.response.edit_message(embed=view.get_embed(), view=view)

    async def next_page(self, interaction: discord.Interaction) -> None:
        view = WarningSettingView(self.guild_id, self.page + 1)
        await interaction.response.edit_message(embed=view.get_embed(), view=view)

    def get_embed(self) -> discord.Embed:
        """
        依目前設定產生定時提醒總覽的 Embed。

        Returns:
            顯示所有提醒排程摘要的 Embed
        """
        embed = discord.Embed(
            title=i18n.get_text("messages.warning_panel_title", self.guild_id),
            description=i18n.get_text("messages.warning_panel_desc", self.guild_id),
            color=discord.Color.purple()
        )

        warnings = get_warnings(self.guild_id)
        page_text = i18n.get_text(
            "labels.page_indicator", self.guild_id, current=self.page + 1, total=self.total_pages
        )
        list_header = i18n.get_text("messages.warning_list_header", self.guild_id, count=len(warnings))
        list_header = f"{list_header} - {page_text}"
        if not warnings:
            embed.add_field(name=list_header,
                            value=i18n.get_text("messages.warning_no_data", self.guild_id), inline=False)
        else:
            embed.add_field(name=list_header, value="​", inline=False)

            warning_items = list(warnings.items())
            page_start = self.page * WARNING_PAGE_SIZE
            for warning_id, warning_data in warning_items[page_start:page_start + WARNING_PAGE_SIZE]:
                title = warning_data.get("content", {}).get("title") or f"*({i18n.get_text('labels.untitled', self.guild_id)})*"
                status_text = i18n.get_text(
                    "labels.warning_active" if warning_data.get("active", True) else "labels.warning_paused",
                    self.guild_id
                )

                channel_text = f"<#{warning_data['channel_id']}>" if warning_data.get("channel_id") else i18n.get_text(
                    "messages.value_not_set", self.guild_id)
                role_text = f"<@&{warning_data['role_id']}>" if warning_data.get("role_id") else i18n.get_text(
                    "labels.none_value", self.guild_id)

                schedule_config = warning_data.get("schedule", {})
                frequency_labels = {
                    "daily": i18n.get_text("labels.freq_daily", self.guild_id),
                    "weekly": i18n.get_text("labels.freq_weekly", self.guild_id),
                    "monthly": i18n.get_text("labels.freq_monthly", self.guild_id),
                }
                frequency_text = frequency_labels.get(
                    schedule_config.get("type"), i18n.get_text("labels.freq_unknown", self.guild_id)
                )
                schedule_text = f"{frequency_text} {schedule_config.get('time', '00:00')}"
                if schedule_config.get("days"):
                    schedule_text += f" ({','.join(map(str, schedule_config['days']))})"

                field_value = i18n.get_text(
                    "messages.warning_field_value",
                    self.guild_id,
                    channel=channel_text,
                    role=role_text,
                    schedule=schedule_text,
                )
                field_name = i18n.get_text(
                    "messages.warning_field_name", self.guild_id, title=title, status=status_text
                )
                embed.add_field(name=field_name, value=field_value, inline=False)

        return embed

