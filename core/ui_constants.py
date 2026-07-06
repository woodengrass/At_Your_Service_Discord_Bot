PANEL_TIMEOUT_SECONDS = 900
MAX_SELECT_OPTIONS = 25
MAX_EMBED_FIELDS = 25
WARNING_PAGE_SIZE = MAX_EMBED_FIELDS - 1
MODAL_TITLE_MAX_LENGTH = 45
TEXT_INPUT_LABEL_MAX_LENGTH = 45
SELECT_OPTION_LABEL_MAX_LENGTH = 100


def truncate_text(text: str, max_length: int) -> str:
    """
    將 Discord UI 文字限制在元件允許的最大長度內。

    Args:
        text: 原始文字
        max_length: 最大字元數

    Returns:
        截斷後的文字
    """
    return text[:max_length]
