import asyncio
import logging
import os
import tempfile

import aiohttp
import discord
from discord.ext import commands
from groq import Groq

from core.config import CONFIG
from core.discord_output import send_ai_text_result
from core.i18n import i18n

logger = logging.getLogger(__name__)

AUDIO_CHUNK_SIZE_BYTES = 64 * 1024


class AudioFileTooLargeError(ValueError):
    """語音附件超過設定大小限制。"""


class VoiceTranscribe(commands.Cog):
    """
    當使用者標記機器人並附上語音檔案（或回覆含語音檔案的訊息）時，透過 Groq API 進行語音轉文字。
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        api_key = os.getenv("GROQ_API_KEY")
        if api_key:
            self.client = Groq(api_key=api_key)
        else:
            self.client = None
            print("[警告] 未設定 GROQ_API_KEY，語音轉文字功能將無法使用。")
        ai_config = CONFIG.get("ai_settings", {})
        self.model = ai_config.get("voice_transcribe_model", "whisper-large-v3")
        self.max_audio_bytes = ai_config.get("voice_max_file_bytes", 20 * 1024 * 1024)
        self.transcription_semaphore = asyncio.Semaphore(ai_config.get("voice_max_concurrency", 2))
        self.session: aiohttp.ClientSession | None = None

    async def cog_load(self) -> None:
        """建立語音附件下載共用的 HTTP session。"""
        timeout = aiohttp.ClientTimeout(total=120, connect=10, sock_read=30)
        self.session = aiohttp.ClientSession(timeout=timeout)

    async def cog_unload(self) -> None:
        """卸載 Cog 時關閉語音附件下載共用的 HTTP session。"""
        if self.session is not None:
            await self.session.close()

    async def _download_attachment(self, attachment: discord.Attachment, temp_filename: str) -> None:
        """
        以固定大小區塊將語音附件串流寫入暫存檔，並在下載途中再次檢查總大小。

        Args:
            attachment: 要下載的 Discord 語音附件
            temp_filename: 暫存檔完整路徑

        Raises:
            AudioFileTooLargeError: 附件大小超過設定上限
            RuntimeError: HTTP session 尚未初始化或下載回應失敗
        """
        if attachment.size > self.max_audio_bytes:
            raise AudioFileTooLargeError
        if self.session is None:
            raise RuntimeError("HTTP session 尚未初始化")

        downloaded_bytes = 0
        async with self.session.get(attachment.url) as response:
            if response.status != 200:
                raise RuntimeError(f"下載語音檔案失敗，狀態碼：{response.status}")
            with open(temp_filename, "wb") as temp_file:
                async for chunk in response.content.iter_chunked(AUDIO_CHUNK_SIZE_BYTES):
                    downloaded_bytes += len(chunk)
                    if downloaded_bytes > self.max_audio_bytes:
                        raise AudioFileTooLargeError
                    temp_file.write(chunk)

    def _transcribe_file(self, filename: str, temp_filename: str) -> str:
        """
        同步讀取暫存語音檔並呼叫 Groq API 進行轉錄，設計成丟進執行緒池執行，避免阻塞事件迴圈。

        Args:
            filename: 原始檔名，用於告訴 API 檔案格式
            temp_filename: 暫存檔案的完整路徑

        Returns:
            轉錄出來的文字內容
        """
        with open(temp_filename, "rb") as audio_file:
            transcription = self.client.audio.transcriptions.create(
                file=(filename, audio_file.read()),
                model=self.model,
                response_format="json",
                prompt=(
                    "Transcribe the audio accurately. Include all relevant punctuation like commas, "
                    "periods, and question marks."
                ),
            )
        return transcription.text

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """
        監聽訊息，當使用者標記機器人且訊息（或回覆的訊息）含有語音附件時觸發轉錄流程。

        Args:
            message: 收到的訊息物件
        """
        if message.author.bot or not message.guild:
            return
        if self.bot.user not in message.mentions:
            return

        target_message = message
        if message.reference:
            if message.reference.cached_message:
                target_message = message.reference.cached_message
            else:
                try:
                    channel = self.bot.get_channel(message.reference.channel_id)
                    target_message = await channel.fetch_message(message.reference.message_id)
                except Exception as e:
                    logger.error(f"取得回覆的原始訊息失敗：{e}", exc_info=True)
                    return

        voice_attachment = None
        audio_extensions = ('.ogg', '.m4a', '.mp3', '.wav', '.flac', '.aac')
        for attachment in target_message.attachments:
            if any(attachment.filename.lower().endswith(ext) for ext in audio_extensions):
                voice_attachment = attachment
                break

        if not voice_attachment:
            return

        if not self.client:
            not_configured_message = i18n.get_text("messages.ai_not_configured", message.guild.id)
            await message.reply(not_configured_message)
            return

        if voice_attachment.size > self.max_audio_bytes:
            await message.reply(i18n.get_text("messages.stt_file_too_large", message.guild.id))
            return

        temp_filename = None
        async with self.transcription_semaphore:
            status_message_text = i18n.get_text("messages.stt_processing", message.guild.id)
            status_message = await message.reply(status_message_text)
            try:
                suffix = os.path.splitext(voice_attachment.filename)[1]
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                    temp_filename = temp_file.name
                await self._download_attachment(voice_attachment, temp_filename)

                final_text = await asyncio.to_thread(
                    self._transcribe_file, voice_attachment.filename, temp_filename
                )
                result_title = i18n.get_text("messages.stt_result_title", message.guild.id)
                await send_ai_text_result(
                    status_message,
                    result_title,
                    final_text,
                    f"{voice_attachment.filename} | Powered by Groq {self.model}",
                    "transcription.txt",
                    discord.Color.green(),
                )
            except AudioFileTooLargeError:
                await status_message.edit(
                    content=i18n.get_text("messages.stt_file_too_large", message.guild.id)
                )
            except Exception as error:
                logger.error(f"語音轉文字失敗：{error}", exc_info=True)
                error_message = i18n.get_text("messages.stt_error", message.guild.id)
                await status_message.edit(content=error_message)
            finally:
                if temp_filename and os.path.exists(temp_filename):
                    try:
                        os.remove(temp_filename)
                    except Exception as error:
                        logger.error(f"刪除暫存語音檔案失敗：{error}", exc_info=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VoiceTranscribe(bot))

