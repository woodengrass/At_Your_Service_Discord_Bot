# Discord Bot 外掛平台

完整架構設計、安全模型、能力 API 規格見 `design.md`（本機檔案，不進版控）。

## 目前狀態（基礎架構骨架）

- `core/database.py`、`core/repository.py`：完整實作，資料表對應 design.md 第 4 節
- `core/manifest.py`：完整實作，含測試（`tests/core/test_manifest.py`）
- `core/quota.py`：完整實作配額檢查邏輯（記憶體滑動視窗），對應 design.md 第 5.4.1 節
- `core/suspension.py`：完整實作停權快取（做法 B），對應 design.md 第 5.5 節
- `core/message_cache.py`：完整實作，對應 design.md 第 4 節「訊息快取層」
- `core/dispatcher.py`：主流程骨架已串起來，但 `_execute_actions` 跟外掛原始碼載入還是 stub
- `core/capability_api.py`：能力登錄表（`CAPABILITY_OWNERS`／`SYNCHRONOUS_FUNCTIONS`）已完整列出附錄 A 全部函式，但實際綁定邏輯待沙箱完成
- `sandbox/`：三個檔案都是 stub，這是第一階段的核心工作
- `bot_integration/`：stub，第二階段工作
- `web/`：極簡骨架（FastAPI health check + schemas 定義），第四階段工作
- `tests/sandbox/`：攻擊測試與資源限制測試目前是 `@pytest.mark.skip` 的空殼，**第一階段必須把這些填滿並拿掉 skip**

## 接下來的工作順序

見 `design.md` 第 7 節完整分階段計畫。當前優先：**第一階段——沙箱引擎獨立開發**：

1. 實作 `sandbox/engine.py`：建立 Lua VM、關閉 `lupa` 的 `register_eval`／`register_builtins`、裝上執行步數與記憶體限制的 hook
2. 把 `tests/sandbox/test_escape_attempts.py`、`test_resource_limits.py` 的 skip 拿掉，逐一實作驗證
3. 沙箱驗證過關後，才實作 `sandbox/capability_bindings.py`，把 `core/capability_api.py` 已定義的函式清單真正綁進 Lua VM
4. 串起 `sandbox/worker.py`，這時 `core/dispatcher.py` 的 `execute_plugin_event` 呼叫就能真正動起來
5. 第二階段開始前，先確認 `core/dispatcher.py` 裡標注「待補」的部分（外掛原始碼載入、`granted_capabilities` 解析、`_execute_actions` 真正呼叫 Discord API）都排進工作清單
