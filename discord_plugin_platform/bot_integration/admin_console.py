"""
終端機管理指令，比照現有 honeypot-discord-bot 專案的 admin/console.py 模式。
第二、三階段開發重點（外掛安裝管理、配額調整），見 design.md 第 5.4.1 節。
"""

HELP_TEXT = """
[外掛平台管理工具] 可用指令（直接在終端機輸入後按 Enter）：
  admin plugin list                                          列出所有已上架外掛
  admin plugin install <guild_id> <plugin_id>                 安裝外掛到指定伺服器
  admin plugin uninstall <guild_id> <plugin_id>               從指定伺服器移除外掛
  admin plugin suspend <plugin_id>                            停權指定外掛（跨所有安裝）
  admin plugin quota set <guild_id> <plugin_id> execution=<次數> action=<次數>
                                                               調整指定安裝的動態配額
"""


async def handle_command(line: str) -> None:
    """
    解析單行終端機指令。

    Args:
        line: 終端機輸入的一整行文字

    Raises:
        NotImplementedError: 待第二階段實作，需先完成 core/repository.py 對應的安裝／
            停權操作函式
    """
    raise NotImplementedError("第二、三階段實作項目")
