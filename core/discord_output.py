import io

import discord

EMBED_DESCRIPTION_MAX_LENGTH = 4096
TWO_EMBED_MAX_LENGTH = EMBED_DESCRIPTION_MAX_LENGTH * 2


def split_text_once(text: str, limit: int = EMBED_DESCRIPTION_MAX_LENGTH) -> tuple[str, str]:
    """
    優先在段落或換行處將文字分成兩段，找不到適合邊界時才依字元數切割。

    Args:
        text: 要切割的完整文字
        limit: 第一段的最大字元數

    Returns:
        第一段與剩餘文字
    """
    if len(text) <= limit:
        return text, ""
    minimum_first_length = max(0, len(text) - limit) if len(text) <= limit * 2 else 0
    split_position = text.rfind("\n\n", minimum_first_length, limit + 1)
    if split_position < minimum_first_length:
        split_position = text.rfind("\n", minimum_first_length, limit + 1)
    if split_position < minimum_first_length:
        split_position = limit
    return text[:split_position], text[split_position:]


async def send_ai_text_result(
    processing_message: discord.Message,
    title: str,
    text: str,
    footer: str,
    attachment_filename: str,
    color: discord.Color,
) -> None:
    """
    將 AI 文字結果控制在最多兩則 Discord 訊息內；極長內容以第二則附件保留完整結果。

    Args:
        processing_message: 原本顯示處理中狀態的訊息
        title: Embed 標題
        text: AI 產生的完整文字
        footer: Embed footer
        attachment_filename: 極長內容使用的 UTF-8 附件檔名
        color: Embed 顏色
    """
    first_part, second_part = split_text_once(text)
    first_embed = discord.Embed(title=title, description=first_part, color=color)
    first_embed.set_footer(text=footer[:2048])
    await processing_message.edit(content="", embed=first_embed)

    if not second_part:
        return
    if len(text) <= TWO_EMBED_MAX_LENGTH:
        second_embed = discord.Embed(title=title, description=second_part, color=color)
        await processing_message.channel.send(embed=second_embed)
        return

    attachment_data = io.BytesIO(second_part.encode("utf-8"))
    await processing_message.channel.send(file=discord.File(attachment_data, filename=attachment_filename))
