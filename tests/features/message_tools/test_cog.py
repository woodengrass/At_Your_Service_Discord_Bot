import datetime
import gzip
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from features.message_tools import cog as message_tools_cog
from features.message_tools.cog import MessageTools


class FakeHistoryMessage:
    """提供聊天匯出測試所需的最小訊息介面。"""

    def __init__(self, content: str, index: int) -> None:
        self.content = content
        self.created_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        self.author = SimpleNamespace(display_name=f"user-{index}")


class FakeExportChannel:
    """模擬可讀取歷史並接收 gzip 分片的 Discord 頻道。"""

    def __init__(self, messages: list[FakeHistoryMessage]) -> None:
        self.name = "general"
        self.mention = "<#100>"
        self.messages = messages
        self.uploaded_contents: list[bytes] = []

    async def history(self, **options: object) -> AsyncIterator[FakeHistoryMessage]:
        """依序產生測試訊息。"""
        for message in self.messages:
            yield message

    async def send(self, content: str, file: discord.File) -> None:
        """在檔案被刪除前讀取並保存上傳的 gzip 內容。"""
        file.fp.seek(0)
        with gzip.GzipFile(fileobj=file.fp, mode="rb") as compressed_file:
            self.uploaded_contents.append(compressed_file.read())


def test_compress_export_file_preserves_content(tmp_path: Path) -> None:
    """聊天匯出分片壓縮後應可完整還原。"""
    source_path = tmp_path / "chat.txt"
    compressed_path = tmp_path / "chat.txt.gz"
    content = "第一則訊息\n第二則訊息\n".encode()
    source_path.write_bytes(content)
    cog = MessageTools()

    cog._compress_export_file(str(source_path), str(compressed_path))

    with gzip.open(compressed_path, "rb") as compressed_file:
        assert compressed_file.read() == content


@pytest.mark.asyncio
async def test_export_chat_streams_and_uploads_multiple_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """大量匯出應逐片上傳，不需先把所有訊息保存在記憶體。"""
    channel = FakeExportChannel(
        [FakeHistoryMessage("x" * 40, index) for index in range(3)]
    )
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=100),
        channel=channel,
        response=SimpleNamespace(defer=AsyncMock()),
        edit_original_response=AsyncMock(),
    )
    monkeypatch.setattr(message_tools_cog, "EXPORT_CHUNK_MAX_BYTES", 80)
    monkeypatch.setattr(message_tools_cog.i18n, "get_text", MagicMock(return_value="message"))
    cog = MessageTools()

    await MessageTools.export_chat.callback(cog, interaction, hours=0, limit=0)

    assert len(channel.uploaded_contents) == 3
    assert b"x" * 40 in channel.uploaded_contents[0]
