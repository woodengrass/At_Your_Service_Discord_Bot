import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def _reset_bot_registry():
    """
    core/bot_registry.py 是模組級單例，測試裡呼叫 set_bot() 註冊假 bot 之後，
    如果忘記清掉會一路殘留到下一個測試（可能是完全不同檔案），讓那個測試
    意外拿到一個不相關的假 bot、排查起來很難查到真正原因。這裡在每個測試
    結束後自動清掉，測試本身不用再手動寫 try/finally: bot_registry.set_bot(None)。
    """
    from core import bot_registry

    yield
    bot_registry.set_bot(None)
