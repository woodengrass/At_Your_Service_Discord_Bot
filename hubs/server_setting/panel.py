import logging

import discord

from core.guild_settings import GuildSettings
from core.i18n import i18n
from core.ui_constants import PANEL_TIMEOUT_SECONDS
from features.delete_log.panel import DeleteLogToggleView
from features.people_counting.panel import PeopleCountChannelSelect

logger = logging.getLogger(__name__)


def format_config_embed(guild_id: int, data: dict) -> discord.Embed:
    """
    將伺服器設定資料轉換為可讀的設定總覽 Embed。

    Args:
        guild_id: 伺服器 ID
        data: 伺服器的完整設定資料（common 與 modules）

    Returns:
        顯示目前設定內容的 Embed
    """
    embed = discord.Embed(
        title=i18n.get_text("ui.view_config", guild_id),
        color=discord.Color.blue(),
    )

    def _recursive_format(sub_data: dict) -> None:
        for key, value in sub_data.items():
            label_key = f"labels.{key}"
            label_text = i18n.get_text(label_key, guild_id)
            display_name = label_text if label_text != label_key else key.capitalize()

            if isinstance(value, dict):
                if key in {"modules", "common"}:
                    _recursive_format(value)
                    continue

                field_lines = []
                for sub_key, sub_value in value.items():
                    display_value = str(sub_value)
                    if "channel_id" in sub_key and sub_value:
                        display_value = f"<#{sub_value}>"
                    elif isinstance(sub_value, bool):
                        display_value = i18n.get_text(
                            "messages.status_enabled" if sub_value else "messages.status_disabled", guild_id
                        )

                    sub_label_key = f"labels.{sub_key}"
                    sub_label = i18n.get_text(sub_label_key, guild_id)
                    sub_display_name = sub_label if sub_label != sub_label_key else sub_key
                    field_lines.append(f"└ {sub_display_name}: {display_value}")

                not_set_text = i18n.get_text("messages.value_not_set", guild_id)
                embed.add_field(
                    name=display_name,
                    value="\n".join(field_lines) or not_set_text,
                    inline=False,
                )
            else:
                if key == "whitelist":
                    continue

                display_value = str(value)
                if "channel_id" in key and value:
                    display_value = f"<#{value}>"

                embed.add_field(
                    name=display_name,
                    value=display_value,
                    inline=True,
                )

    _recursive_format(data)
    return embed


class AnnouncementChannelSelect(discord.ui.ChannelSelect):
    """
    公告頻道選擇器，選擇後將該頻道設定為伺服器的公告日誌頻道。
    """

    def __init__(self, guild_id: int, parent_view: "ServerSettingView") -> None:
        self.guild_id = guild_id
        self.parent_view = parent_view
        super().__init__(
            placeholder=i18n.get_text("ui.select_announcement_channel", guild_id),
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected_channel = self.values[0]
        channel = interaction.guild.get_channel(selected_channel.id)
        if not channel:
            error_message = i18n.get_text("messages.error_channel_not_found", self.guild_id)
            await interaction.response.send_message(error_message, ephemeral=True)
            return

        await GuildSettings.set_log_channel(self.guild_id, str(channel.id))

        success_message = i18n.get_text("messages.success_announcement", self.guild_id)
        embed = discord.Embed(
            title=i18n.get_text("messages.title_setting_success", self.guild_id),
            description=success_message,
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(embed=self.parent_view.get_embed(), view=self.parent_view)
        await interaction.followup.send(embed=embed, ephemeral=True)


class LanguageSelect(discord.ui.Select):
    """
    伺服器語言選擇器。
    """

    def __init__(self, guild_id: int, parent_view: "ServerSettingView") -> None:
        self.guild_id = guild_id
        self.parent_view = parent_view
        super().__init__(
            placeholder=i18n.get_text("ui.select_language", guild_id),
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=i18n.get_text("labels.language_zh_tw", guild_id), value="zh-TW"),
                discord.SelectOption(label=i18n.get_text("labels.language_zh_cn", guild_id), value="zh-CN"),
                discord.SelectOption(label=i18n.get_text("labels.language_en_us", guild_id), value="en-US"),
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await i18n.set_lang(self.guild_id, self.values[0])
        success_message = i18n.get_text("messages.lang_set", self.guild_id)
        embed = discord.Embed(
            title=i18n.get_text("messages.title_language_updated", self.guild_id),
            description=success_message,
            color=discord.Color.blue(),
        )
        await interaction.response.edit_message(embed=self.parent_view.get_embed(), view=self.parent_view)
        await interaction.followup.send(embed=embed, ephemeral=True)


class ServerSettingSubView(discord.ui.View):
    """
    顯示單一伺服器設定元件並提供返回主面板按鈕。
    """

    def __init__(self, guild_id: int, parent_view: "ServerSettingView", item: discord.ui.Item) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.parent_view = parent_view
        self.add_item(item)
        back_button = discord.ui.Button(
            label=i18n.get_text("ui.back", guild_id),
            style=discord.ButtonStyle.secondary,
        )
        back_button.callback = self.back_to_main
        self.add_item(back_button)

    async def back_to_main(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(embed=self.parent_view.get_embed(), view=self.parent_view)


class ServerSettingSelect(discord.ui.Select):
    """
    伺服器設定主選單，作為各項子設定的入口。
    """

    def __init__(self, guild_id: int, parent_view: "ServerSettingView") -> None:
        self.guild_id = guild_id
        self.parent_view = parent_view

        options = [
            discord.SelectOption(
                label=i18n.get_text("ui.view_config", guild_id),
                description=i18n.get_text("ui.desc_config", guild_id),
                value="view_config",
            ),
            discord.SelectOption(
                label=i18n.get_text("ui.set_people_count", guild_id),
                value="set_people_count",
            ),
            discord.SelectOption(
                label=i18n.get_text("ui.set_announcement", guild_id),
                value="set_announcement",
            ),
            discord.SelectOption(
                label=i18n.get_text("ui.toggle_delete_log", guild_id),
                value="toggle_delete_log",
            ),
            discord.SelectOption(
                label=i18n.get_text("ui.set_language", guild_id),
                value="set_language",
            ),
        ]

        super().__init__(
            placeholder=i18n.get_text("ui.placeholder", guild_id),
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        handlers = {
            "view_config": self._handle_view_config,
            "set_people_count": self._handle_set_people_count,
            "set_announcement": self._handle_set_announcement,
            "toggle_delete_log": self._handle_toggle_delete_log,
            "set_language": self._handle_set_language,
        }

        handler = handlers.get(self.values[0])
        if handler:
            await handler(interaction)

    async def _handle_view_config(self, interaction: discord.Interaction) -> None:
        data = GuildSettings.get_guild_data(self.guild_id)
        embed = format_config_embed(self.guild_id, data)
        await interaction.response.edit_message(embed=embed, view=self.parent_view)

    async def _handle_set_people_count(self, interaction: discord.Interaction) -> None:
        view = ServerSettingSubView(
            self.guild_id, self.parent_view, PeopleCountChannelSelect(self.guild_id, self.parent_view)
        )
        await interaction.response.edit_message(
            content=i18n.get_text("ui.select_channel_count", self.guild_id),
            embed=None,
            view=view,
        )

    async def _handle_set_announcement(self, interaction: discord.Interaction) -> None:
        view = ServerSettingSubView(
            self.guild_id, self.parent_view, AnnouncementChannelSelect(self.guild_id, self.parent_view)
        )
        await interaction.response.edit_message(
            content=i18n.get_text("ui.select_announcement_channel", self.guild_id),
            embed=None,
            view=view,
        )

    async def _handle_toggle_delete_log(self, interaction: discord.Interaction) -> None:
        view = DeleteLogToggleView(self.guild_id, self.parent_view)
        await interaction.response.edit_message(
            content=i18n.get_text("ui.select_delete_log", self.guild_id),
            embed=None,
            view=view,
        )

    async def _handle_set_language(self, interaction: discord.Interaction) -> None:
        view = ServerSettingSubView(
            self.guild_id, self.parent_view, LanguageSelect(self.guild_id, self.parent_view)
        )
        await interaction.response.edit_message(
            content=i18n.get_text("ui.select_language", self.guild_id),
            embed=None,
            view=view,
        )


class ServerSettingView(discord.ui.View):
    """
    伺服器設定面板的最上層 View。
    """

    def __init__(self, guild_id: int) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.guild_id = guild_id
        self.add_item(ServerSettingSelect(guild_id, self))

    def get_embed(self) -> discord.Embed:
        """
        取得目前伺服器設定總覽。

        Returns:
            伺服器設定總覽 Embed
        """
        return format_config_embed(self.guild_id, GuildSettings.get_guild_data(self.guild_id))



