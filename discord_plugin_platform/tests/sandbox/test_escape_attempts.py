"""
沙箱逃逸攻擊測試套件，對應 design.md 第 5.3 節列出的已知手法，每一種手法一個測試案例。
這是第一階段的驗收標準之一，不是可有可無的測試。
"""

import pytest


@pytest.mark.skip(reason="待第一階段 sandbox/engine.py 完成後撰寫")
def test_cannot_reach_global_table_via_debug_library():
    """
    嘗試透過 debug.getupvalue/getmetatable/getfenv 爬回原始環境，確認拿不到 _G。
    """


@pytest.mark.skip(reason="待第一階段 sandbox/engine.py 完成後撰寫")
def test_load_and_loadstring_are_disabled():
    """
    嘗試呼叫 load/loadstring 動態載入程式碼，確認被擋下。
    """


@pytest.mark.skip(reason="待第一階段 sandbox/engine.py 完成後撰寫")
def test_lupa_python_bridge_is_disabled():
    """
    嘗試透過 python.eval 或傳入物件的屬性存取呼叫回 Python 層，確認完全不可行。
    """


@pytest.mark.skip(reason="待第一階段 sandbox/engine.py 完成後撰寫")
def test_single_call_memory_bomb_is_capped():
    """
    嘗試用 string.rep 之類的單一呼叫瞬間分配大量記憶體，確認有另外的參數大小上限攔截。
    """
