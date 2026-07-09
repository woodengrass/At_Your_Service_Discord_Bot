import asyncio
import logging
import threading
from multiprocessing.connection import Connection
from typing import Any

from core.capability_api import CapabilityBackend
from core.capability_errors import ScheduledTaskLimitExceededError, StorageLimitExceededError

logger = logging.getLogger(__name__)

_SERIALIZED_ERROR_TYPES = (
    StorageLimitExceededError,
    ScheduledTaskLimitExceededError,
    RuntimeError,
    ValueError,
)


async def serve_capability_requests(connection: Connection, backend: CapabilityBackend) -> dict[str, Any]:
    """
    服務子行程透過 pipe 送來的同步能力請求，直到收到 done 訊息為止。

    Args:
        connection: 主行程端 pipe connection
        backend: 實際處理能力請求的後端

    Returns:
        子行程送回的 done 訊息
    """
    loop = asyncio.get_running_loop()
    done_future: asyncio.Future[dict[str, Any]] = loop.create_future()

    def reader() -> None:
        _serve_blocking(connection, backend, loop, done_future)

    thread = threading.Thread(target=reader, name="plugin-capability-rpc", daemon=True)
    thread.start()
    try:
        return await done_future
    finally:
        connection.close()


def _serve_blocking(
    connection: Connection,
    backend: CapabilityBackend,
    loop: asyncio.AbstractEventLoop,
    done_future: asyncio.Future[dict[str, Any]],
) -> None:
    """
    在背景 thread 裡阻塞讀取 pipe 訊息並同步處理能力請求。

    Args:
        connection: 主行程端 pipe connection
        backend: 實際處理能力請求的後端
        loop: 主 asyncio event loop，用於安全設定 future 結果
        done_future: 收到 done 或 pipe 斷線時要完成的 future
    """
    while True:
        try:
            message = connection.recv()
        except (EOFError, OSError) as error:
            loop.call_soon_threadsafe(_set_exception_once, done_future, RuntimeError(f"子行程管線已中斷：{error}"))
            return

        kind = message.get("kind")
        if kind == "request":
            response = _handle_request(backend, message)
            try:
                connection.send(response)
            except (BrokenPipeError, EOFError, OSError) as error:
                loop.call_soon_threadsafe(_set_exception_once, done_future, RuntimeError(f"回傳能力回應失敗：{error}"))
                return
            continue
        if kind == "done":
            loop.call_soon_threadsafe(_set_result_once, done_future, message)
            return

        logger.error(f"收到未知的沙箱 RPC 訊息：{message}")
        loop.call_soon_threadsafe(_set_exception_once, done_future, RuntimeError(f"未知的 RPC 訊息類型：{kind}"))
        return


def _handle_request(backend: CapabilityBackend, message: dict[str, Any]) -> dict[str, Any]:
    """
    處理單一能力請求並序列化結果或例外。

    Args:
        backend: 實際處理能力請求的後端
        message: 子行程送來的 request 訊息

    Returns:
        response 訊息
    """
    method_name = message.get("method")
    args = message.get("args", {})
    try:
        method = getattr(backend, method_name)
        result = method(**args)
        return {"kind": "response", "result": result}
    except _SERIALIZED_ERROR_TYPES as error:
        return {
            "kind": "response",
            "error": {"type": error.__class__.__name__, "message": str(error)},
        }
    except Exception as error:
        logger.error(f"處理沙箱能力請求失敗：{error}", exc_info=True)
        return {
            "kind": "response",
            "error": {"type": "RuntimeError", "message": str(error)},
        }


def _set_result_once(done_future: asyncio.Future[dict[str, Any]], result: dict[str, Any]) -> None:
    """
    Future 尚未完成時設定結果，避免取消/逾時後重複設定。
    """
    if not done_future.done():
        done_future.set_result(result)


def _set_exception_once(done_future: asyncio.Future[dict[str, Any]], error: Exception) -> None:
    """
    Future 尚未完成時設定例外，避免取消/逾時後重複設定。
    """
    if not done_future.done():
        done_future.set_exception(error)
