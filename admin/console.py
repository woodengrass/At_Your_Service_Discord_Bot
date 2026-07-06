from discord.ext import commands

from core import console_dispatcher
from features.link_checker.repository import add_keyword, get_all_keywords, remove_keyword
from core.audit_log_repository import delete_user_logs

HELP_TEXT = """
[管理員終端機工具] 可用指令（直接在終端機輸入後按 Enter）：
  admin help                       顯示這個說明
  admin keyword list               列出所有連結檢查可疑關鍵字
  admin keyword add <keyword>      新增連結檢查可疑關鍵字
  admin keyword remove <keyword>   移除連結檢查可疑關鍵字
  admin gdpr delete <user_id>      刪除指定使用者在所有伺服器的稽核紀錄
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
        parts = line.split()
        if parts[0] != "admin":
            return

        if len(parts) < 2 or parts[1] == "help":
            print(HELP_TEXT)
            return

        if parts[1] == "keyword":
            await self._handle_keyword_command(parts[2:])
        elif parts[1] == "gdpr":
            await self._handle_gdpr_command(parts[2:])
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

    async def _refresh_link_checker_cache(self) -> None:
        """
        通知 LinkChecker Cog 重新載入關鍵字快取，讓新增/移除的關鍵字立即生效。
        """
        link_checker_cog = self.bot.get_cog("LinkChecker")
        if link_checker_cog:
            await link_checker_cog.reload_keywords()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminConsole(bot))

