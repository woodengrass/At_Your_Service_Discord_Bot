from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Self

import pytest

from features.voice_transcribe.cog import AudioFileTooLargeError, VoiceTranscribe


class FakeContent:
    """模擬 aiohttp 串流回應內容。"""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def iter_chunked(self, chunk_size: int) -> AsyncIterator[bytes]:
        """依序產生預先指定的資料區塊。"""
        for chunk in self.chunks:
            yield chunk


class FakeResponse:
    """模擬可作為 async context manager 的 HTTP 回應。"""

    def __init__(self, chunks: list[bytes], status: int = 200) -> None:
        self.status = status
        self.content = FakeContent(chunks)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object | None,
    ) -> None:
        return None


class FakeSession:
    """模擬語音下載使用的 HTTP session。"""

    def __init__(self, response: FakeResponse) -> None:
        self.response = response

    def get(self, url: str) -> FakeResponse:
        """回傳固定 HTTP 回應。"""
        return self.response


@pytest.mark.asyncio
async def test_download_attachment_rejects_declared_oversized_file(tmp_path: Path) -> None:
    """Discord 宣告的附件大小超限時，不應啟動下載。"""
    cog = object.__new__(VoiceTranscribe)
    cog.max_audio_bytes = 5
    cog.session = None
    attachment = SimpleNamespace(size=6, url="https://cdn.example/audio.mp3")

    with pytest.raises(AudioFileTooLargeError):
        await cog._download_attachment(attachment, str(tmp_path / "audio.mp3"))


@pytest.mark.asyncio
async def test_download_attachment_stops_when_stream_exceeds_limit(tmp_path: Path) -> None:
    """實際下載資料超過限制時，即使附件宣告較小也必須中止。"""
    cog = object.__new__(VoiceTranscribe)
    cog.max_audio_bytes = 5
    cog.session = FakeSession(FakeResponse([b"abc", b"def"]))
    attachment = SimpleNamespace(size=4, url="https://cdn.example/audio.mp3")

    with pytest.raises(AudioFileTooLargeError):
        await cog._download_attachment(attachment, str(tmp_path / "audio.mp3"))
