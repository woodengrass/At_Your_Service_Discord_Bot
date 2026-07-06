import json
import logging
import os

logger = logging.getLogger(__name__)


def load_json(file_path: str) -> dict:
    """
    讀取指定路徑的 JSON 檔案。

    Args:
        file_path: JSON 檔案路徑

    Returns:
        dict，檔案內容；若檔案不存在則回傳空字典

    Raises:
        Exception: 檔案存在但無法讀取或解析
    """
    if not os.path.exists(file_path):
        return {}
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception as error:
        logger.error(f"讀取 JSON 失敗（{file_path}）：{error}", exc_info=True)
        raise
