from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from core.discord_output import send_ai_text_result, split_text_once


def test_split_text_once_preserves_complete_text() -> None:
    """文字切割後重新組合應保留完整內容順序。"""
    text = "a" * 3000 + "\n\n" + "b" * 2000

    first_part, second_part = split_text_once(text)

    assert first_part + second_part == text
    assert len(first_part) <= 4096


def test_split_text_once_keeps_both_embed_parts_within_limit() -> None:
    """兩個 Embed 可容納的文字不得因過早換行而讓第二段超限。"""
    text = "a" * 3000 + "\n" + "b" * 5191

    first_part, second_part = split_text_once(text)

    assert first_part + second_part == text
    assert len(first_part) <= 4096
    assert len(second_part) <= 4096


@pytest.mark.asyncio
async def test_send_ai_text_result_uses_two_embeds_for_medium_result() -> None:
    """超過單一 Embed 的 AI 結果應分成兩則 Embed。"""
    channel = SimpleNamespace(send=AsyncMock())
    message = SimpleNamespace(edit=AsyncMock(), channel=channel)

    await send_ai_text_result(
        message,
        "title",
        "a" * 5000,
        "footer",
        "result.txt",
        discord.Color.blue(),
    )

    message.edit.assert_awaited_once()
    channel.send.assert_awaited_once()
    assert "embed" in channel.send.await_args.kwargs


@pytest.mark.asyncio
async def test_send_ai_text_result_uses_attachment_for_extreme_result() -> None:
    """超過兩個 Embed 容量的結果應在第二則訊息附上剩餘完整內容。"""
    channel = SimpleNamespace(send=AsyncMock())
    message = SimpleNamespace(edit=AsyncMock(), channel=channel)

    await send_ai_text_result(
        message,
        "title",
        "a" * 9000,
        "footer",
        "result.txt",
        discord.Color.blue(),
    )

    sent_file = channel.send.await_args.kwargs["file"]
    assert sent_file.filename == "result.txt"
