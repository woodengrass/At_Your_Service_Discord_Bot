from collections import deque

from core import quota


def test_clear_usage_removes_single_installation_counters() -> None:
    """
    解除單一安裝時應清掉該安裝的執行與動作配額紀錄。
    """
    quota._execution_timestamps[(1111, "plugin_a")] = deque([1.0])
    quota._action_timestamps[(1111, "plugin_a")] = deque([1.0])
    quota._execution_timestamps[(1111, "plugin_b")] = deque([1.0])

    quota.clear_usage(1111, "plugin_a")

    assert (1111, "plugin_a") not in quota._execution_timestamps
    assert (1111, "plugin_a") not in quota._action_timestamps
    assert (1111, "plugin_b") in quota._execution_timestamps
    quota._execution_timestamps.clear()
    quota._action_timestamps.clear()


def test_clear_guild_usage_removes_all_guild_counters() -> None:
    """
    機器人離開伺服器時應清掉該伺服器所有外掛的配額紀錄。
    """
    quota._execution_timestamps[(1111, "plugin_a")] = deque([1.0])
    quota._action_timestamps[(1111, "plugin_b")] = deque([1.0])
    quota._execution_timestamps[(2222, "plugin_a")] = deque([1.0])

    quota.clear_guild_usage(1111)

    assert (1111, "plugin_a") not in quota._execution_timestamps
    assert (1111, "plugin_b") not in quota._action_timestamps
    assert (2222, "plugin_a") in quota._execution_timestamps
    quota._execution_timestamps.clear()
    quota._action_timestamps.clear()
