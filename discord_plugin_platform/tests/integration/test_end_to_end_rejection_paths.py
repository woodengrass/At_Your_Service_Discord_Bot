"""
端到端整合測試：宿主端動作驗證拒絕沙箱回傳的動作清單時（`rejected_invalid_action`），
驗證 storage 寫入確實被回退（見 design.md 第 5.4.2 節）。

真正惡意的「呼叫未授權能力」這條路徑，在真的沙箱綁定層下其實打不通：
`sandbox/capability_bindings.py` 的 `get_allowed_functions()` 只把使用者同意過的
能力函式綁進 Lua 的 `api` 表，沒授權的函式根本不存在於沙箱裡（見 design.md 第
11 節 A.3），外掛呼叫 `api.add_role(...)` 這種沒授權的函式只會撞到「呼叫 nil
值」的 Lua 錯誤，最終落到 `crashed`，不是 `rejected_invalid_action`——
`_validate_actions()` 對「未授權能力」的檢查是縱深防禦最後一道防線，防的是
「沙箱綁定層本身被繞過」這種更嚴重的情境（tests/core/test_dispatcher_storage_rollback.py
直接 monkeypatch execute_plugin_event() 模擬這種情境）。

單次執行超過附錄 A 規定的 20 個動作上限這條路，在真實架構下也打不通到
`rejected_invalid_action`：`core/capability_api.py` 的 `ExecutionContext.queue_action()`
本身在沙箱子行程裡排第 21 個動作的當下就直接 `raise RuntimeError`（見
`MAX_ACTIONS_PER_EXECUTION` 的檢查），子行程還沒回傳就已經整個崩潰，
最終落到的是 `crashed`，`_validate_actions()` 的數量上限檢查同樣是防
「沙箱那層檢查被繞過」用的縱深防禦，不是外掛正常執行路徑上打得到的分支。

這裡改用真的能從合法呼叫已授權能力、但傳錯參數型別觸發的路徑：
`_build_message_functions()` 的 `send_message` 綁定不檢查 `content` 的型別，
單純把 Lua 傳進來的值原封不動塞進動作佇列；`core/dispatcher.py` 的
`_validate_action_param_values()` 才是真正檢查 `content` 必須是字串的地方。
外掛不小心把數字當內容傳進去（例如忘記轉字串就送出計數器數值），是「作者疏忽但
無惡意」這個威脅情境的真實例子（design.md 第 5.1 節），會在宿主驗證層被擋下、
標記為 `rejected_invalid_action`，同一次執行裡已經寫入的 storage 資料也要一併回退。
"""

from core import bot_registry, database, dispatcher, plugin_storage_repository
from tests.integration._fakes import FakeBot, FakeChannel, FakeGuild, Recorder, get_execution_log_rows, install_plugin

GUILD_ID = 900003


async def _setup_db(tmp_path, monkeypatch, name: str):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / name))
    await database.init_db()


async def test_non_string_content_is_rejected_and_storage_write_rolls_back(tmp_path, monkeypatch):
    await _setup_db(tmp_path, monkeypatch, "rejection.db")

    recorder = Recorder()
    channel = FakeChannel(recorder, 42)
    guild = FakeGuild(GUILD_ID, recorder, channels={42: channel})
    bot_registry.set_bot(FakeBot(guild))

    plugin_id = "buggy_plugin"
    await install_plugin(
        GUILD_ID,
        plugin_id,
        source_code="""
        function on_message(payload)
            api.storage_set("attempted", true)
            -- 忘了把數字轉成字串就直接送出，content 應該是字串，這裡傳的是數字
            api.send_message(payload.channel_id, 123)
        end
        """,
        event_hooks=["on_message"],
        granted_capabilities=["storage", "send_message"],
    )

    result = await dispatcher.dispatch_event(GUILD_ID, "on_message", {"channel_id": 42})

    assert result is False
    assert recorder.calls == []  # 動作清單整批被拒絕，一個訊息都沒有真的送出

    assert await plugin_storage_repository.storage_get(GUILD_ID, plugin_id, "attempted") is None

    logs = await get_execution_log_rows(GUILD_ID, plugin_id)
    assert len(logs) == 1
    assert logs[0]["outcome"] == "rejected_invalid_action"

    await database.close_db()
