import json
import logging
import os

logger = logging.getLogger(__name__)

CONFIG_PATH = "config/config.json"


def load_config() -> dict:
    """
    讀取本地設定檔 config/config.json。

    Returns:
        dict，設定檔內容；若檔案不存在或讀取失敗則回傳空字典
    """
    if not os.path.exists(CONFIG_PATH):
        logger.warning(f"找不到設定檔：{CONFIG_PATH}，將使用預設值。")
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception as error:
        logger.error(f"讀取設定檔失敗：{error}", exc_info=True)
        return {}


CONFIG = load_config()
