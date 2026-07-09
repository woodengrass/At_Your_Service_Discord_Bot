"""
core/capability_api.py 的 ExecutionContext.run_coroutine_sync() 測試：
驗證跨執行緒呼叫逾時時，會嘗試取消還在主 event loop 上跑的 coroutine，
不會留著孤兒 coroutine 繼續執行（可能對著已經被 dispatcher rollback／關閉的
execution_db 連線寫東西，見 design.md 第 5.4.2 節、第 12.3 節）。
"""

import asyncio
import threading

import pytest

import core.capability_api as capability_api_module
from core.capability_api import ExecutionContext


def _run_loop_in_background_thread() -> tuple[asyncio.AbstractEventLoop, threading.Thread]:
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    return loop, thread


def test_run_coroutine_sync_cancels_orphaned_coroutine_on_timeout(monkeypatch):
    monkeypatch.setattr(capability_api_module, "CROSS_THREAD_CALL_TIMEOUT_SECONDS", 0.05)

    loop, thread = _run_loop_in_background_thread()
    try:
        completed = threading.Event()

        async def slow_coroutine():
            try:
                await asyncio.sleep(1)
                completed.set()
            except asyncio.CancelledError:
                raise

        context = ExecutionContext(
            guild_id=1,
            plugin_id="test_plugin",
            granted_capabilities=set(),
            bot=None,
            event_loop=loop,
        )

        with pytest.raises(TimeoutError):
            context.run_coroutine_sync(slow_coroutine())

        # 取消是合作式的，給主 loop 一點時間真正把 coroutine 停掉。
        cancel_confirmed = threading.Event()

        def _check_cancelled():
            cancel_confirmed.set()

        loop.call_soon_threadsafe(_check_cancelled)
        cancel_confirmed.wait(timeout=1)

        # slow_coroutine() 應該在 sleep(1) 完成前就被取消，never 真的跑到 completed.set()。
        assert not completed.wait(timeout=0.5)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=1)
        loop.close()
