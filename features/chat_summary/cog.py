import asyncio
import logging
import os

import discord
from discord.ext import commands
from groq import Groq

from core.config import CONFIG
from core.discord_output import send_ai_text_result
from core.i18n import i18n

logger = logging.getLogger(__name__)


class ChatSummary(commands.Cog):
    """
    當使用者標記機器人並回覆某則訊息時，透過 Groq API 分析該訊息之後的對話並產生摘要。
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        api_key = os.getenv("GROQ_API_KEY")
        if api_key:
            self.client = Groq(api_key=api_key)
        else:
            self.client = None
            print("[警告] 未設定 GROQ_API_KEY，聊天摘要功能無法使用。")
        ai_config = CONFIG.get("ai_settings", {})
        self.model = ai_config.get("chat_summary_model", "llama-3.3-70b-versatile")
        self.history_limit = ai_config.get("chat_history_limit", 500)

    def _format_chat_message(self, chat_message: discord.Message) -> str:
        """
        將訊息格式化為「[時間] 使用者：內容」的紀錄格式。

        Args:
            chat_message: 欲格式化的訊息物件

        Returns:
            格式化後的文字
        """
        time_str = chat_message.created_at.strftime("%Y-%m-%d %H:%M")
        return f"[{time_str}] {chat_message.author.display_name}: {chat_message.content}"

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """
        監聽訊息，當使用者標記機器人並回覆某則訊息時觸發聊天摘要流程。

        Args:
            message: 收到的訊息物件
        """
        if message.author.bot or not message.guild:
            return
        if self.bot.user not in message.mentions:
            return
        if not message.reference:
            return

        target_message = None
        if message.reference.cached_message:
            target_message = message.reference.cached_message
        else:
            try:
                channel = self.bot.get_channel(message.reference.channel_id)
                target_message = await channel.fetch_message(message.reference.message_id)
            except Exception as e:
                logger.error(f"取得回覆的原始訊息失敗：{e}", exc_info=True)
                return

        if target_message.author == self.bot.user:
            return

        audio_extensions = ('.ogg', '.m4a', '.mp3', '.wav', '.flac', '.aac')
        if target_message.attachments:
            for attachment in target_message.attachments:
                if attachment.filename.lower().endswith(audio_extensions):
                    return

        if not self.client:
            not_configured_message = i18n.get_text("messages.ai_not_configured", message.guild.id)
            await message.reply(not_configured_message)
            return

        status_message = i18n.get_text("messages.summary_analyzing", message.guild.id)
        processing_message = await message.reply(status_message)

        try:
            chat_log = [self._format_chat_message(target_message)]
            async for history_message in message.channel.history(after=target_message, limit=self.history_limit):
                if not history_message.content.strip():
                    continue
                chat_log.append(self._format_chat_message(history_message))

            if len(chat_log) < 2:
                await processing_message.edit(content=i18n.get_text("messages.summary_no_content", message.guild.id))
                return

            full_text = "\n".join(chat_log)

            lang_code = i18n.get_lang(message.guild.id)
            lang_prompt = "Traditional Chinese (繁體中文)"
            if lang_code == "zh-CN":
                lang_prompt = "Simplified Chinese (簡體中文)"
            elif lang_code == "en-US":
                lang_prompt = "English"

            prompt = (
                f"你是一位精通語意分析與對話脈絡梳理的專業助理。請針對以下 Discord 聊天紀錄進行「結構化深度摘要」。\n"
                f"請使用 {lang_prompt} 輸出，並嚴格遵循以下邏輯：\n\n"
                f"### 核心規則\n"
                f"1. **標題格式**：開頭必須寫「# 此對話流程摘要如下：」。\n"
                f"2. **機器人過濾**：用戶「隨叫隨到#2091」是系統機器人，請**完全忽略**它的所有發言，"
                f"不要將其計入摘要，也不須告訴用戶你忽略的此機器人。\n"
                f"3. **客觀陳述**：你僅負責總結紀錄中「實際發生」的對話。若對話中未提及解決方案，"
                f"請勿自行腦補或嘗試解決問題。\n\n"
                f"### 內容分析指南\n"
                f"1. **層級化結構 (重要)**：\n"
                f"   - 針對長時間的對話，請識別出 **「主要討論主題」** 作為一級標題。\n"
                f"   - 在每個大主題下，使用項目符號列出 **「關鍵細節」**。\n"
                f"2. **深度與技術細節**：\n"
                f"   - 詳細描述討論的起因、核心爭議點、技術限制（如 gt 時序、紅石元件特性等）。\n"
                f"   - **明確標註觀點持有者**：例如「用戶 A 提出...但用戶 B 反駁...」。\n"
                f"3. **語氣識別**：\n"
                f"   - 請具備識別「開玩笑」、「諷刺」或「梗圖互動」的能力。"
                f"若某段對話純屬娛樂（如玩梗、互相吐槽），請將其歸類為「閒聊」並簡略帶過，"
                f"不要誤將其當作嚴肅的技術建議。\n\n"
                f"### 輸出範例格式\n"
                f"**1. [主要主題名稱]**\n"
                f"   - **討論起源**：[用戶名] 詢問了關於...\n"
                f"   - **核心爭議**：[用戶A] 認為...，但 [用戶B] 指出...\n"
                f"   - **技術細節**：涉及了 [具體技術名詞] 的限制。\n"
                f"   - **結論/狀態**：最終大家決定...\n\n"
                f"**2. [閒聊主題名稱]**\n"
                f"   - [用戶C] 開了一個關於...的玩笑，引發大家討論。\n\n"
                f"聊天紀錄內容：\n{full_text}"
            )

            # Groq SDK 是同步呼叫，用 to_thread 丟到執行緒池執行，避免卡住整個事件迴圈
            completion = await asyncio.to_thread(
                self.client.chat.completions.create,
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a helpful assistant. Please summarize the chat log in "
                            f"{lang_prompt} using bullet points."
                        ),
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )
            summary_text = completion.choices[0].message.content

            title = i18n.get_text("messages.summary_result_title", message.guild.id)
            await send_ai_text_result(
                processing_message,
                title,
                summary_text,
                f"Analyzed {len(chat_log)} messages | Powered by Groq {self.model}",
                "chat-summary.txt",
                discord.Color.blue(),
            )

        except Exception as e:
            logger.error(f"聊天摘要生成失敗：{e}", exc_info=True)
            error_text = i18n.get_text("messages.ai_error", message.guild.id)
            await processing_message.edit(content=error_text)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ChatSummary(bot))

