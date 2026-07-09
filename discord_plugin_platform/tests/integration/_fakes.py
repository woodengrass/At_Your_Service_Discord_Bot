"""
真端到端整合測試共用的假 discord.py 物件與安裝輔助函式。

跟 tests/core/test_dispatcher_execute_actions.py、tests/sandbox/test_process_isolation.py
用的假物件是同一套風格（guild.get_channel/get_member/get_role 反查、
recorder 記錄呼叫），這裡集中一份給 tests/integration/ 底下的檔案共用，
避免三個測試檔各自複製一份幾乎一樣的假物件。
"""

import json

from core import repository
from core.database import get_db


class Recorder:
    def __init__(self):
        self.calls = []


class FakeMessage:
    def __init__(self, recorder, message_id):
        self.id = message_id
        self._recorder = recorder

    async def reply(self, **kwargs):
        self._recorder.calls.append(("reply", self.id, kwargs))

    async def edit(self, **kwargs):
        self._recorder.calls.append(("edit", self.id, kwargs))


class FakeChannel:
    def __init__(self, recorder, channel_id, messages=None):
        self.id = channel_id
        self._recorder = recorder
        self._messages = messages or {}

    async def send(self, **kwargs):
        self._recorder.calls.append(("send", self.id, kwargs))

    async def fetch_message(self, message_id):
        return self._messages[message_id]


class FakeRole:
    def __init__(self, role_id, name="role", position=0):
        self.id = role_id
        self.name = name
        self.position = position


class FakeMember:
    def __init__(self, recorder, user_id, display_name="member", roles=None):
        self.id = user_id
        self.display_name = display_name
        self.joined_at = None
        self.bot = False
        self.roles = roles or []
        self._recorder = recorder

    async def add_roles(self, role):
        self._recorder.calls.append(("add_roles", self.id, role.id))

    async def remove_roles(self, role):
        self._recorder.calls.append(("remove_roles", self.id, role.id))


class FakeGuild:
    def __init__(self, guild_id, recorder, channels=None, members=None, roles=None):
        self.id = guild_id
        self.name = "測試伺服器"
        self.member_count = len(members or {})
        self._recorder = recorder
        self._channels = channels or {}
        self._members = members or {}
        self._roles = roles or {}

    def get_channel(self, channel_id):
        return self._channels.get(channel_id)

    def get_member(self, user_id):
        return self._members.get(user_id)

    def get_role(self, role_id):
        return self._roles.get(role_id)


class FakeBot:
    def __init__(self, guild):
        self._guild = guild

    def get_guild(self, guild_id):
        return self._guild if guild_id == self._guild.id else None


async def install_plugin(
    guild_id: int,
    plugin_id: str,
    source_code: str,
    event_hooks: list[str],
    granted_capabilities: list[str],
    version: str = "1.0.0",
    author_id: int = 1,
) -> None:
    """
    比照真實提交/審核/安裝流程，透過 core/repository.py 的真實函式把一個外掛
    裝到指定伺服器：提交版本 → 核准 → 建立安裝紀錄。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        source_code: 外掛 Lua 原始碼
        event_hooks: manifest 的 event_hooks 清單
        granted_capabilities: 使用者同意授權的能力清單
        version: 外掛版本
        author_id: 作者 Discord 使用者 ID
    """
    manifest_json = json.dumps(
        {
            "name": plugin_id,
            "version": version,
            "description": "整合測試外掛",
            "capability_api_version": 1,
            "event_hooks": event_hooks,
            "required_capabilities": granted_capabilities,
        },
        ensure_ascii=False,
    )
    await repository.submit_plugin_version(
        plugin_id=plugin_id,
        author_id=author_id,
        name=plugin_id,
        version=version,
        manifest_json=manifest_json,
        source_code=source_code,
        capability_api_version=1,
    )
    await repository.approve_plugin(plugin_id)
    await repository.create_installation(guild_id, plugin_id, version, granted_capabilities)


async def get_execution_log_rows(guild_id: int, plugin_id: str) -> list[dict]:
    """
    直接查 plugin_execution_log，供測試斷言稽核紀錄的 outcome/error 用。
    repository.py 目前只有 log_execution() 這個寫入函式，沒有對應的查詢函式
    （查詢介面是之後後台管理應用才會需要的東西，見 design.md 第 3.5 節），
    測試直接查表即可，不用為了這個新增一個生產程式碼還用不到的查詢函式。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID

    Returns:
        list of dict，依 log_id 由舊到新排序
    """
    db = get_db()
    async with db.execute(
        """
        SELECT event_type, actions_json, execution_ms, outcome, error
        FROM plugin_execution_log
        WHERE guild_id = ? AND plugin_id = ?
        ORDER BY log_id
        """,
        (guild_id, plugin_id),
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        {
            "event_type": row[0],
            "actions_json": row[1],
            "execution_ms": row[2],
            "outcome": row[3],
            "error": row[4],
        }
        for row in rows
    ]
