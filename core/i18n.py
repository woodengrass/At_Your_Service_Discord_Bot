import logging

import discord
from discord import app_commands
from discord.app_commands import locale_str

from core.file_io import load_json
from core.guild_settings import GuildSettings

logger = logging.getLogger(__name__)

LANG_FILE = "locales/languages.json"


class I18n(app_commands.Translator):
    """
    處理多語言文字查詢，並提供斜線指令名稱/描述的動態翻譯。
    """

    def __init__(self) -> None:
        self.translations = self.load_translations()

    def load_translations(self) -> dict:
        """
        讀取語言字典檔案。

        Returns:
            dict，完整的語言字典內容
        """
        return load_json(LANG_FILE)

    async def set_lang(self, guild_id: int, lang: str) -> None:
        """
        設定指定伺服器使用的語言。

        Args:
            guild_id: 伺服器 ID
            lang: 語言代碼
        """
        await GuildSettings.set_language(guild_id, lang)

    def get_lang(self, guild_id: int) -> str:
        """
        取得指定伺服器目前使用的語言代碼。

        Args:
            guild_id: 伺服器 ID

        Returns:
            語言代碼字串
        """
        return GuildSettings.get_language(guild_id)

    def get_text(self, key: str, guild_id: int, **kwargs: object) -> str:
        """
        依照指定伺服器的語言設定，取得對應的文字內容。

        Args:
            key: 語言字典鍵值，格式為「分類.鍵值」
            guild_id: 伺服器 ID
            **kwargs: 用於格式化文字內容的參數

        Returns:
            對應語言的文字內容；若查詢失敗則回傳原始 key
        """
        lang = self.get_lang(guild_id)
        keys = key.split(".")
        data = self.translations
        try:
            for single_key in keys:
                data = data[single_key]
            text = data.get(lang, data.get("zh-TW", str(key)))

            return text.format(**kwargs)
        except Exception as error:
            logger.error(f"翻譯文字失敗（key={key}, guild_id={guild_id}）：{error}", exc_info=True)
            return str(key)

    async def translate(
        self,
        string: locale_str,
        locale: discord.Locale,
        context: app_commands.TranslationContext
    ) -> str | None:
        """
        供 discord.py 的 app_commands 系統呼叫，翻譯斜線指令名稱與描述。

        Args:
            string: 待翻譯的原始字串物件
            locale: 目標語言
            context: 翻譯情境（名稱或描述）

        Returns:
            翻譯後的文字；若找不到對應翻譯則回傳 None
        """
        try:
            key = string.message
            if "." in key:
                translation_data = self.translations
                for key_part in key.split("."):
                    translation_data = translation_data[key_part]
                return translation_data.get(str(locale), translation_data.get("zh-TW"))

            if key in self.translations.get("commands", {}):
                command_data = self.translations["commands"][key]
                target_lang = str(locale)

                if target_lang in command_data.get("name", {}):
                    if context.location == app_commands.TranslationContextLocation.command_name:
                        return command_data["name"][target_lang]

                if target_lang in command_data.get("description", {}):
                    if context.location == app_commands.TranslationContextLocation.command_description:
                        return command_data["description"][target_lang]
            return None
        except Exception as error:
            logger.error(f"翻譯應用程式指令失敗（key={string.message}）：{error}", exc_info=True)
            return None


i18n = I18n()

