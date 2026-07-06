import asyncio
import logging
import os
import shlex

import imagehash
from discord.ext import commands
from PIL import Image

from core import console_dispatcher
from core.audit_log_repository import delete_user_logs
from features.link_checker.repository import (
    add_keyword,
    add_scam_hash,
    get_all_keywords,
    get_all_scam_hashes,
    remove_keyword,
    remove_scam_hash,
)

logger = logging.getLogger(__name__)

IMAGE_FILE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")

HELP_TEXT = """
[管理員終端機工具] 可用指令（直接在終端機輸入後按 Enter）：
  admin help                              顯示這個說明
  admin keyword list                      列出所有連結檢查可疑關鍵字
  admin keyword add <keyword>             新增連結檢查可疑關鍵字
  admin keyword remove <keyword>          移除連結檢查可疑關鍵字
  admin gdpr delete <user_id>             刪除指定使用者在所有伺服器的稽核紀錄
  admin scamimage list                    列出所有已知詐騙圖片的雜湊
  admin scamimage add <圖片路徑> [標籤]     新增一張詐騙圖片
  admin scamimage remove <圖片路徑>        依圖片內容移除對應的雜湊紀錄
  admin scamimage sync <資料夾路徑>        掃描整個資料夾，匯入尚未加入的圖片（可重複執行）
  路徑或關鍵字中間有空白時，請用雙引號包住，例如：admin scamimage sync "C:\path\scam image"
"""


class AdminConsole(commands.Cog):
    """
    機器人擁有者專用的終端機管理工具：連結檢查關鍵字黑名單管理、GDPR 稽核紀錄刪除請求。
    這些操作只能透過能存取機器人執行主機終端機的人執行，不透過 Discord 指令對外開放。
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        """
        Cog 載入時把自己的指令處理函式掛到共用終端機指令分派器，並印出可用指令說明。
        """
        console_dispatcher.register_handler(self._handle_command)
        await console_dispatcher.start(self.bot)
        print(HELP_TEXT)

    async def _handle_command(self, line: str) -> None:
        """
        解析單行終端機指令（keyword/gdpr）。

        Args:
            line: 終端機輸入的一整行文字
        """
        if not line:
            return
        try:
            parts = shlex.split(line)
        except ValueError as error:
            print(f"[管理員終端機工具] 指令解析失敗，請檢查引號是否配對：{error}")
            return
        if not parts or parts[0] != "admin":
            return

        if len(parts) < 2 or parts[1] == "help":
            print(HELP_TEXT)
            return

        if parts[1] == "keyword":
            await self._handle_keyword_command(parts[2:])
        elif parts[1] == "gdpr":
            await self._handle_gdpr_command(parts[2:])
        elif parts[1] == "scamimage":
            await self._handle_scamimage_command(parts[2:])
        else:
            print(f"[管理員終端機工具] 不認得的指令：{line}，輸入 'admin help' 看說明。")

    async def _handle_keyword_command(self, args: list[str]) -> None:
        """
        處理 keyword 子指令：list/add/remove。

        Args:
            args: "keyword" 之後的參數列表
        """
        if not args:
            print("[管理員終端機工具] 請指定 keyword 子指令：list/add/remove。")
            return

        sub_command = args[0]

        if sub_command == "list":
            keywords = await get_all_keywords()
            if not keywords:
                print("[管理員終端機工具] 目前沒有任何關鍵字。")
                return
            print("[管理員終端機工具] 目前的可疑關鍵字：")
            for keyword in sorted(keywords):
                print(f"  {keyword}")
            return

        if len(args) < 2:
            print("[管理員終端機工具] 請提供要新增/移除的關鍵字。")
            return
        keyword = args[1].lower().strip()

        if sub_command == "add":
            added = await add_keyword(keyword)
            if added:
                await self._refresh_link_checker_cache()
                print(f"[管理員終端機工具] 已新增關鍵字：{keyword}")
            else:
                print(f"[管理員終端機工具] 關鍵字已存在：{keyword}")
        elif sub_command == "remove":
            removed = await remove_keyword(keyword)
            if removed:
                await self._refresh_link_checker_cache()
                print(f"[管理員終端機工具] 已移除關鍵字：{keyword}")
            else:
                print(f"[管理員終端機工具] 找不到關鍵字：{keyword}")
        else:
            print(f"[管理員終端機工具] 不認得的 keyword 子指令：{sub_command}")

    async def _handle_gdpr_command(self, args: list[str]) -> None:
        """
        處理 gdpr 子指令：delete。

        Args:
            args: "gdpr" 之後的參數列表
        """
        if len(args) < 2 or args[0] != "delete":
            print("[管理員終端機工具] 用法：admin gdpr delete <user_id>")
            return

        try:
            user_id = int(args[1])
        except ValueError:
            print("[管理員終端機工具] user_id 必須是數字。")
            return

        deleted_count = await delete_user_logs(user_id)
        print(f"[管理員終端機工具] 已刪除使用者 {user_id} 的 {deleted_count} 筆稽核紀錄。")

    async def _handle_scamimage_command(self, args: list[str]) -> None:
        """
        處理 scamimage 子指令：list/add/remove/sync。

        Args:
            args: "scamimage" 之後的參數列表
        """
        if not args:
            print("[管理員終端機工具] 請指定 scamimage 子指令：list/add/remove/sync。")
            return

        sub_command = args[0]

        if sub_command == "list":
            scam_hashes = await get_all_scam_hashes()
            if not scam_hashes:
                print("[管理員終端機工具] 目前沒有任何詐騙圖片雜湊。")
                return
            print(f"[管理員終端機工具] 目前有 {len(scam_hashes)} 筆詐騙圖片雜湊：")
            for phash, label in scam_hashes:
                print(f"  {phash}  {label or ''}")
            return

        if sub_command == "sync":
            if len(args) < 2:
                print("[管理員終端機工具] 用法：admin scamimage sync <資料夾路徑>")
                return
            await self._sync_scam_image_folder(args[1])
            return

        if sub_command == "add":
            if len(args) < 2:
                print("[管理員終端機工具] 用法：admin scamimage add <圖片路徑> [標籤]")
                return
            label = args[2] if len(args) > 2 else os.path.basename(args[1])
            await self._add_scam_image_file(args[1], label)
            return

        if sub_command == "remove":
            if len(args) < 2:
                print("[管理員終端機工具] 用法：admin scamimage remove <圖片路徑>")
                return
            await self._remove_scam_image_file(args[1])
            return

        print(f"[管理員終端機工具] 不認得的 scamimage 子指令：{sub_command}")

    def _compute_image_hash(self, file_path: str) -> str | None:
        """
        計算單一圖片檔案的感知雜湊值，內含檔案 I/O 與 CPU-bound 運算，呼叫端應以 asyncio.to_thread 執行。

        Args:
            file_path: 圖片檔案路徑

        Returns:
            感知雜湊值的十六進位字串；讀取或計算失敗則回傳 None
        """
        try:
            with Image.open(file_path) as image:
                return str(imagehash.phash(image))
        except Exception as error:
            logger.error(f"計算圖片雜湊失敗（{file_path}）：{error}", exc_info=True)
            return None

    async def _add_scam_image_file(self, file_path: str, label: str) -> None:
        """
        將單一圖片檔案的感知雜湊新增到詐騙圖片資料庫。

        Args:
            file_path: 圖片檔案路徑
            label: 用於辨識來源的標籤
        """
        phash = await asyncio.to_thread(self._compute_image_hash, file_path)
        if phash is None:
            print(f"[管理員終端機工具] 無法讀取圖片：{file_path}")
            return

        added = await add_scam_hash(phash, label)
        if added:
            await self._refresh_link_checker_scam_hashes()
            print(f"[管理員終端機工具] 已新增詐騙圖片雜湊：{file_path}（標籤：{label}）")
        else:
            print(f"[管理員終端機工具] 這張圖片的雜湊已經存在，略過：{file_path}")

    async def _remove_scam_image_file(self, file_path: str) -> None:
        """
        依圖片內容找出對應的感知雜湊並從詐騙圖片資料庫移除。

        Args:
            file_path: 圖片檔案路徑
        """
        phash = await asyncio.to_thread(self._compute_image_hash, file_path)
        if phash is None:
            print(f"[管理員終端機工具] 無法讀取圖片：{file_path}")
            return

        removed = await remove_scam_hash(phash)
        if removed:
            await self._refresh_link_checker_scam_hashes()
            print(f"[管理員終端機工具] 已移除詐騙圖片雜湊：{file_path}")
        else:
            print(f"[管理員終端機工具] 找不到對應的雜湊紀錄：{file_path}")

    async def _sync_scam_image_folder(self, folder_path: str) -> None:
        """
        掃描整個資料夾，將尚未匯入的圖片新增到詐騙圖片資料庫。設計成可重複執行，
        已存在的雜湊會自動略過，方便日後持續加入新的詐騙圖片樣本。

        Args:
            folder_path: 資料夾路徑
        """
        if not os.path.isdir(folder_path):
            print(f"[管理員終端機工具] 找不到資料夾：{folder_path}")
            return

        added_count = 0
        skipped_count = 0
        for filename in sorted(os.listdir(folder_path)):
            if not filename.lower().endswith(IMAGE_FILE_EXTENSIONS):
                continue
            file_path = os.path.join(folder_path, filename)
            phash = await asyncio.to_thread(self._compute_image_hash, file_path)
            if phash is None:
                continue
            added = await add_scam_hash(phash, filename)
            if added:
                added_count += 1
            else:
                skipped_count += 1

        await self._refresh_link_checker_scam_hashes()
        print(
            f"[管理員終端機工具] 掃描完成，資料夾：{folder_path}，"
            f"新增 {added_count} 筆，略過（已存在）{skipped_count} 筆。"
        )

    async def _refresh_link_checker_scam_hashes(self) -> None:
        """
        通知 LinkChecker Cog 重新載入詐騙圖片雜湊快取，讓新增/移除的雜湊立即生效。
        """
        link_checker_cog = self.bot.get_cog("LinkChecker")
        if link_checker_cog:
            await link_checker_cog.reload_scam_hashes()

    async def _refresh_link_checker_cache(self) -> None:
        """
        通知 LinkChecker Cog 重新載入關鍵字快取，讓新增/移除的關鍵字立即生效。
        """
        link_checker_cog = self.bot.get_cog("LinkChecker")
        if link_checker_cog:
            await link_checker_cog.reload_keywords()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminConsole(bot))

