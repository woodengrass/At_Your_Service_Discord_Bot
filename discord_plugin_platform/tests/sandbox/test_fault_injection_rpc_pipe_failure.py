"""
故障注入：sandbox/rpc_server.py 的管線協定本身中途壞掉——子行程死掉/關閉連線,
或送出不符合協定的訊息,而不是正常收到 "kind": "done"。design.md 第 11 節 Track G
G.3.1 定義了三種訊息（request/response/done），但沒有明講「完全沒收到 done、
管線直接斷掉」這種情況要怎麼處理；sandbox/rpc_server.py 的 _serve_blocking()
已經對 EOFError/OSError 做了處理（見程式碼），這裡驗證這個處理確實把管線中斷
轉成乾淨的錯誤，而不是讓等待方永遠掛住，或讓例外不受控制地一路炸到
dispatch_event() 上層。

tests/sandbox/test_process_isolation.py::test_process_worker_timeout_terminates_child
測的是「Lua 執行卡住,逾時機制介入」；這裡測的是完全不同的故障模式——管線本身
斷掉，跟逾時無關（子行程可能瞬間死掉，遠早於逾時期限）。
"""

import multiprocessing
import time

import pytest

from core import bot_registry
from sandbox import rpc_server, worker
from sandbox.engine import SandboxExecutionError
from sandbox.worker import execute_plugin_event


class _FakeGuild:
    id = 1
    name = "測試伺服器"
    member_count = 0

    def get_member(self, user_id):
        return None

    def get_channel(self, channel_id):
        return None

    def get_role(self, role_id):
        return None


class _FakeBot:
    def get_guild(self, guild_id):
        return _FakeGuild() if guild_id == 1 else None


@pytest.fixture
def fake_bot():
    bot = _FakeBot()
    bot_registry.set_bot(bot)
    return bot


def _child_dies_without_sending_done(connection, *args) -> None:
    """
    模擬子行程崩潰/被殺死，完全沒有送出 "kind": "done" 就直接關閉管線，
    比照真正的 LuaJIT 直譯器層級 crash（Python try/except 攔不到的那種）。
    """
    connection.close()


def _child_sends_garbage_then_exits(connection, *args) -> None:
    """
    模擬子行程送出不符合協定的訊息（不是 request/response/done 其中一種），
    驗證主行程不會被未知訊息卡住等待，而是視為協定錯誤乾淨中止。
    """
    connection.send({"kind": "this_is_not_a_real_protocol_message"})
    connection.close()


async def test_execute_plugin_event_surfaces_child_death_as_sandbox_error_not_hang(monkeypatch, fake_bot):
    """
    子行程死掉、管線斷掉時 conn.recv() 會丟 EOFError，_serve_blocking() 應該把它
    轉成乾淨的錯誤，execute_plugin_event() 最終應該拋 SandboxExecutionError，
    而不是讓呼叫端永遠等下去、也不是讓原始的 EOFError 未經包裝就往外炸。
    """
    monkeypatch.setattr(worker, "_child_process_main", _child_dies_without_sending_done)

    with pytest.raises(SandboxExecutionError):
        await execute_plugin_event(
            guild_id=1,
            plugin_id="test_plugin",
            source_code="function on_message(payload) end",
            event_type="on_message",
            event_payload={},
            granted_capabilities=set(),
        )


async def test_execute_plugin_event_surfaces_unknown_protocol_message_as_sandbox_error(monkeypatch, fake_bot):
    """
    子行程送出協定外的訊息時，同樣應該收斂成 SandboxExecutionError，不是掛住
    或讓未知例外往外炸穿。
    """
    monkeypatch.setattr(worker, "_child_process_main", _child_sends_garbage_then_exits)

    with pytest.raises(SandboxExecutionError):
        await execute_plugin_event(
            guild_id=1,
            plugin_id="test_plugin",
            source_code="function on_message(payload) end",
            event_type="on_message",
            event_payload={},
            granted_capabilities=set(),
        )


async def test_execute_plugin_event_joins_process_even_when_pipe_breaks(monkeypatch, fake_bot):
    """
    管線斷掉導致 SandboxExecutionError 之後，子行程仍然要被完整回收
    （process.join()），不能因為走的是例外路徑就漏掉清理，否則長期運行會
    累積殭屍行程（design.md 第 11 節 Track G.5 特別強調的資源洩漏風險）。
    """
    monkeypatch.setattr(worker, "_child_process_main", _child_dies_without_sending_done)

    with pytest.raises(SandboxExecutionError):
        await execute_plugin_event(
            guild_id=1,
            plugin_id="test_plugin",
            source_code="function on_message(payload) end",
            event_type="on_message",
            event_payload={},
            granted_capabilities=set(),
        )

    # 給作業系統一點時間更新行程表；join() 已經在 execute_plugin_event() 內完成，
    # 這裡只是確認沒有任何存活的子行程殘留。
    time.sleep(0.1)
    assert not any(child.is_alive() for child in multiprocessing.active_children())


class _RecordingBackend:
    """
    測試用 CapabilityBackend：不會真的被呼叫到，_serve_blocking 在收到
    done/未知訊息/管線中斷時都不需要真的服務任何請求。
    """


async def test_serve_capability_requests_raises_when_child_closes_pipe_early():
    """
    直接測 rpc_server.serve_capability_requests()（不透過完整的 worker 子行程),
    子行程端在完全沒有送任何請求或 done 訊息的情況下就直接關閉管線,
    父行程這端應該乾淨地拋出例外，而不是永遠卡在等待 done。
    """
    parent_connection, child_connection = multiprocessing.Pipe(duplex=True)
    child_connection.close()

    with pytest.raises(RuntimeError, match="中斷"):
        await rpc_server.serve_capability_requests(parent_connection, _RecordingBackend())


async def test_serve_capability_requests_raises_on_unknown_message_kind():
    """
    子行程送出一個 "kind" 不是 request/done 的訊息時，serve_capability_requests()
    應該拋出例外指出未知的訊息類型，而不是忽略或掛住。
    """
    parent_connection, child_connection = multiprocessing.Pipe(duplex=True)
    child_connection.send({"kind": "unexpected"})
    child_connection.close()

    with pytest.raises(RuntimeError, match="未知的 RPC 訊息類型"):
        await rpc_server.serve_capability_requests(parent_connection, _RecordingBackend())
