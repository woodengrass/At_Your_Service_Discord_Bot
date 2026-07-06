import discord

from core.guild_settings import GuildSettings
from core.i18n import i18n
from core.ui_constants import PANEL_TIMEOUT_SECONDS


class AntiSpamToggleView(discord.ui.View):
    """
    防洗版功能的開關儀表板，可分別切換總開關、跨頻道偵測與同頻道偵測。
    """

    def __init__(self, guild_id: int) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.guild_id = guild_id
        self.update_buttons()

    def update_buttons(self) -> None:
        """
        依目前設定重建三個切換按鈕。
        """
        self.clear_items()
        config = GuildSettings.get_module_config(self.guild_id, "anti_spam")
        master_enabled = config.get("enabled", True)
        multi_channel_enabled = config.get("enable_multi_channel", True)
        same_channel_enabled = config.get("enable_same_channel", True)

        self.add_item(self._create_toggle_button("master", master_enabled))
        self.add_item(self._create_toggle_button("multi", multi_channel_enabled))
        self.add_item(self._create_toggle_button("single", same_channel_enabled))
        self._add_back_button()

    def _create_toggle_button(self, config_key: str, current_state: bool) -> discord.ui.Button:
        """
        建立單一切換按鈕。

        Args:
            config_key: 對應的開關識別字串（master/multi/single）
            current_state: 目前的開關狀態
        Returns:
            設定好樣式與回呼的按鈕元件
        """
        style = discord.ButtonStyle.success if current_state else discord.ButtonStyle.danger
        state_key = "ui.state_on" if current_state else "ui.state_off"
        state_text = i18n.get_text(state_key, self.guild_id)

        if config_key == "master":
            label_key = "ui.btn_spam_master"
        elif config_key == "multi":
            label_key = "ui.btn_spam_multi"
        else:
            label_key = "ui.btn_spam_single"

        label = f"{i18n.get_text(label_key, self.guild_id)}: {state_text}"
        button = discord.ui.Button(label=label, style=style, custom_id=config_key)

        async def callback(interaction: discord.Interaction) -> None:
            new_state = not current_state
            db_key_map = {"master": "enabled", "multi": "enable_multi_channel", "single": "enable_same_channel"}
            await GuildSettings.set_module_config(self.guild_id, "anti_spam", db_key_map[config_key], new_state)
            self.update_buttons()
            await interaction.response.edit_message(view=self)
            feature = i18n.get_text(label_key, self.guild_id)
            status = i18n.get_text("ui.state_on" if new_state else "ui.state_off", self.guild_id)
            await interaction.followup.send(
                i18n.get_text("messages.setting_status_updated", self.guild_id, feature=feature, status=status),
                ephemeral=True,
            )

        button.callback = callback
        return button

    def _add_back_button(self) -> None:
        back_button = discord.ui.Button(
            label=i18n.get_text("ui.btn_back", self.guild_id),
            style=discord.ButtonStyle.secondary,
        )
        back_button.callback = self.back_to_main
        self.add_item(back_button)

    def _main_view(self) -> discord.ui.View:
        from hubs.anti_fraud.panel import AntiFraudView
        return AntiFraudView(self.guild_id)

    async def back_to_main(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content=None, embed=None, view=self._main_view())



