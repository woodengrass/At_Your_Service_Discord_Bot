from typing import Any

from core import guild_settings_repository


class SettingsManager:
    """
    管理所有伺服器（guild）的通用設定與各模組設定，資料庫為 SQLite，
    記憶體中維護一份與舊版 JSON 結構相容的快取（讀取用，透過 load_cache() 載入）。
    """

    def __init__(self) -> None:
        self.data: dict[str, dict] = {}

    async def load_cache(self) -> None:
        """
        從資料庫載入全部伺服器設定到記憶體快取，機器人啟動時呼叫一次。
        """
        self.data = await guild_settings_repository.get_all_data()

    def get_guild_data(self, guild_id: int) -> dict:
        """
        取得指定伺服器的設定資料，若不存在則建立預設值。

        Args:
            guild_id: 伺服器 ID

        Returns:
            dict，包含 common 與 modules 兩個區塊
        """
        guild_id_str = str(guild_id)
        if guild_id_str not in self.data:
            self.data[guild_id_str] = {
                "common": {
                    "log_channel_id": None,
                    "whitelist": [],
                    "language": "zh-TW"
                },
                "modules": {}
            }
        return self.data[guild_id_str]

    def get_log_channel(self, guild_id: int) -> str | None:
        """
        取得指定伺服器的公告日誌頻道 ID。

        Args:
            guild_id: 伺服器 ID

        Returns:
            頻道 ID 字串，若尚未設定則回傳 None
        """
        return self.get_guild_data(guild_id)["common"].get("log_channel_id")

    async def set_log_channel(self, guild_id: int, channel_id: int) -> None:
        """
        設定指定伺服器的公告日誌頻道。

        Args:
            guild_id: 伺服器 ID
            channel_id: 頻道 ID
        """
        value = str(channel_id)
        await guild_settings_repository.set_value(guild_id, "common", "log_channel_id", value)
        self.get_guild_data(guild_id)["common"]["log_channel_id"] = value

    def get_whitelist(self, guild_id: int) -> list[str]:
        """
        取得指定伺服器的白名單使用者 ID 列表。

        Args:
            guild_id: 伺服器 ID

        Returns:
            list of str，使用者 ID
        """
        return self.get_guild_data(guild_id)["common"].get("whitelist", [])

    async def add_whitelist(self, guild_id: int, user_id: int) -> bool:
        """
        將使用者加入指定伺服器的白名單。

        Args:
            guild_id: 伺服器 ID
            user_id: 使用者 ID

        Returns:
            True 表示成功加入；若使用者已在白名單中則回傳 False
        """
        common_data = self.get_guild_data(guild_id)["common"]
        if "whitelist" not in common_data:
            common_data["whitelist"] = []
        user_id_str = str(user_id)
        if user_id_str not in common_data["whitelist"]:
            common_data["whitelist"].append(user_id_str)
            await guild_settings_repository.set_value(guild_id, "common", "whitelist", common_data["whitelist"])
            return True
        return False

    async def remove_whitelist(self, guild_id: int, user_id: int) -> bool:
        """
        將使用者從指定伺服器的白名單移除。

        Args:
            guild_id: 伺服器 ID
            user_id: 使用者 ID

        Returns:
            True 表示成功移除；若使用者不在白名單中則回傳 False
        """
        common_data = self.get_guild_data(guild_id)["common"]
        user_id_str = str(user_id)
        if user_id_str in common_data.get("whitelist", []):
            common_data["whitelist"].remove(user_id_str)
            await guild_settings_repository.set_value(guild_id, "common", "whitelist", common_data["whitelist"])
            return True
        return False

    def get_language(self, guild_id: int) -> str:
        """
        取得指定伺服器目前使用的語言代碼。

        Args:
            guild_id: 伺服器 ID

        Returns:
            語言代碼字串，預設為 zh-TW
        """
        return self.get_guild_data(guild_id)["common"].get("language", "zh-TW")

    async def set_language(self, guild_id: int, lang: str) -> None:
        """
        設定指定伺服器使用的語言。

        Args:
            guild_id: 伺服器 ID
            lang: 語言代碼
        """
        await guild_settings_repository.set_value(guild_id, "common", "language", lang)
        self.get_guild_data(guild_id)["common"]["language"] = lang

    def get_module_config(self, guild_id: int, module_name: str) -> dict:
        """
        取得指定伺服器中某個模組的設定。

        Args:
            guild_id: 伺服器 ID
            module_name: 模組名稱

        Returns:
            dict，該模組的設定內容；若尚未設定則回傳空字典
        """
        return self.get_guild_data(guild_id)["modules"].get(module_name, {})

    async def set_module_config(self, guild_id: int, module_name: str, key: str, value: Any) -> None:
        """
        設定指定伺服器中某個模組的設定值。

        Args:
            guild_id: 伺服器 ID
            module_name: 模組名稱
            key: 設定鍵值
            value: 設定內容
        """
        await guild_settings_repository.set_value(guild_id, module_name, key, value)
        guild_data = self.get_guild_data(guild_id)
        if module_name not in guild_data["modules"]:
            guild_data["modules"][module_name] = {}
        guild_data["modules"][module_name][key] = value


GuildSettings = SettingsManager()
