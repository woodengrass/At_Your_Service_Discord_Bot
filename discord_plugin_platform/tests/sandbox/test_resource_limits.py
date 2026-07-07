"""
資源限制驗證測試，對應 design.md 第 5.4 節的步數/記憶體/逾時上限。
"""

import pytest


@pytest.mark.skip(reason="待第一階段 sandbox/engine.py 完成後撰寫")
def test_infinite_loop_is_stopped_by_instruction_limit():
    """
    執行一個無窮迴圈的外掛，確認在 INSTRUCTION_LIMIT 步數內被強制中止。
    """


@pytest.mark.skip(reason="待第一階段 sandbox/engine.py 完成後撰寫")
def test_memory_bomb_is_stopped_by_memory_limit():
    """
    執行一個持續配置記憶體的外掛，確認在 MEMORY_LIMIT_BYTES 內被強制中止。
    """
