"""
core/dispatcher.py 的 _execute_actions() 測試：驗證每種動作類型都對應到正確的
discord.py 呼叫，以及單一動作失敗不會中止整批動作的處理。

不用手動清理 bot_registry：tests/conftest.py 的 _reset_bot_registry autouse
fixture 每個測試結束後都會自動 bot_registry.set_bot(None)。
"""

from core import bot_registry, dispatcher


class _Recorder:
    def __init__(self):
        self.calls = []


class _FakeMessage:
    def __init__(self, recorder, message_id):
        self.id = message_id
        self._recorder = recorder

    async def reply(self, **kwargs):
        self._recorder.calls.append(("reply", self.id, kwargs))

    async def edit(self, **kwargs):
        self._recorder.calls.append(("edit", self.id, kwargs))

    async def pin(self):
        self._recorder.calls.append(("pin", self.id))

    async def unpin(self):
        self._recorder.calls.append(("unpin", self.id))

    async def delete(self):
        self._recorder.calls.append(("delete", self.id))


class _FakeChannel:
    def __init__(self, recorder, channel_id, messages=None, fail_fetch=False):
        self.id = channel_id
        self._recorder = recorder
        self._messages = messages or {}
        self._fail_fetch = fail_fetch

    async def send(self, **kwargs):
        self._recorder.calls.append(("send", self.id, kwargs))

    async def fetch_message(self, message_id):
        if self._fail_fetch:
            raise RuntimeError("找不到訊息")
        return self._messages[message_id]

    async def create_thread(self, **kwargs):
        self._recorder.calls.append(("create_thread", self.id, kwargs))


class _FakeRole:
    def __init__(self, role_id):
        self.id = role_id


class _FakeMember:
    def __init__(self, recorder, user_id):
        self.id = user_id
        self._recorder = recorder

    async def add_roles(self, role):
        self._recorder.calls.append(("add_roles", self.id, role.id))

    async def remove_roles(self, role):
        self._recorder.calls.append(("remove_roles", self.id, role.id))

    async def edit(self, **kwargs):
        self._recorder.calls.append(("edit_member", self.id, kwargs))

    async def timeout(self, until, reason=None):
        self._recorder.calls.append(("timeout", self.id, reason))


class _FakeThread:
    def __init__(self, recorder, thread_id):
        self.id = thread_id
        self._recorder = recorder

    async def edit(self, **kwargs):
        self._recorder.calls.append(("edit_thread", self.id, kwargs))


class _FakeGuild:
    def __init__(self, recorder, channels=None, members=None, roles=None, threads=None):
        self.id = 1
        self._recorder = recorder
        self._channels = channels or {}
        self._members = members or {}
        self._roles = roles or {}
        self._threads = threads or {}

    def get_channel(self, channel_id):
        return self._channels.get(channel_id)

    def get_member(self, user_id):
        return self._members.get(user_id)

    def get_role(self, role_id):
        return self._roles.get(role_id)

    def get_thread(self, thread_id):
        return self._threads.get(thread_id)


class _FakeBot:
    def __init__(self, guild):
        self._guild = guild

    def get_guild(self, guild_id):
        return self._guild if guild_id == self._guild.id else None


async def test_send_message_calls_channel_send():
    recorder = _Recorder()
    channel = _FakeChannel(recorder, 42)
    guild = _FakeGuild(recorder, channels={42: channel})
    bot_registry.set_bot(_FakeBot(guild))

    await dispatcher._execute_actions(1, [{"type": "send_message", "params": {"channel_id": 42, "content": "hi"}}])

    assert recorder.calls == [("send", 42, {"content": "hi", "embed": None, "view": None})]


async def test_reply_edit_pin_unpin_use_channel_id_to_fetch_message():
    recorder = _Recorder()
    message = _FakeMessage(recorder, 100)
    channel = _FakeChannel(recorder, 42, messages={100: message})
    guild = _FakeGuild(recorder, channels={42: channel})
    bot_registry.set_bot(_FakeBot(guild))

    await dispatcher._execute_actions(
        1,
        [
            {"type": "reply_message", "params": {"channel_id": 42, "message_id": 100, "content": "r"}},
            {"type": "edit_message", "params": {"channel_id": 42, "message_id": 100, "content": "e"}},
            {"type": "pin_message", "params": {"channel_id": 42, "message_id": 100}},
            {"type": "unpin_message", "params": {"channel_id": 42, "message_id": 100}},
        ],
    )

    call_kinds = [call[0] for call in recorder.calls]
    assert call_kinds == ["reply", "edit", "pin", "unpin"]


async def test_add_and_remove_role():
    recorder = _Recorder()
    member = _FakeMember(recorder, 999)
    role = _FakeRole(5)
    guild = _FakeGuild(recorder, members={999: member}, roles={5: role})
    bot_registry.set_bot(_FakeBot(guild))

    await dispatcher._execute_actions(
        1,
        [
            {"type": "add_role", "params": {"user_id": 999, "role_id": 5}},
            {"type": "remove_role", "params": {"user_id": 999, "role_id": 5}},
        ],
    )

    assert recorder.calls == [("add_roles", 999, 5), ("remove_roles", 999, 5)]


async def test_timeout_member_and_set_nickname():
    recorder = _Recorder()
    member = _FakeMember(recorder, 999)
    guild = _FakeGuild(recorder, members={999: member})
    bot_registry.set_bot(_FakeBot(guild))

    await dispatcher._execute_actions(
        1,
        [
            {"type": "timeout_member", "params": {"user_id": 999, "duration_seconds": 60, "reason": "spam"}},
            {"type": "set_nickname", "params": {"user_id": 999, "nickname": "new"}},
        ],
    )

    assert recorder.calls == [("timeout", 999, "spam"), ("edit_member", 999, {"nick": "new"})]


async def test_send_poll_builds_discord_poll_with_answers():
    import discord

    recorder = _Recorder()
    channel = _FakeChannel(recorder, 42)
    guild = _FakeGuild(recorder, channels={42: channel})
    bot_registry.set_bot(_FakeBot(guild))

    await dispatcher._execute_actions(
        1,
        [
            {
                "type": "send_poll",
                "params": {
                    "channel_id": 42,
                    "question": "最愛的顏色？",
                    "options": ["紅", "藍"],
                    "duration": 2,
                },
            }
        ],
    )

    assert len(recorder.calls) == 1
    call_kind, channel_id, kwargs = recorder.calls[0]
    assert (call_kind, channel_id) == ("send", 42)
    poll = kwargs["poll"]
    assert isinstance(poll, discord.Poll)
    assert poll.question == "最愛的顏色？"
    assert [answer.text for answer in poll.answers] == ["紅", "藍"]


async def test_delete_message_fetches_then_deletes():
    recorder = _Recorder()
    message = _FakeMessage(recorder, 100)
    channel = _FakeChannel(recorder, 42, messages={100: message})
    guild = _FakeGuild(recorder, channels={42: channel})
    bot_registry.set_bot(_FakeBot(guild))

    await dispatcher._execute_actions(1, [{"type": "delete_message", "params": {"channel_id": 42, "message_id": 100}}])

    assert recorder.calls == [("delete", 100)]


async def test_create_thread_calls_channel_create_thread():
    recorder = _Recorder()
    channel = _FakeChannel(recorder, 42)
    guild = _FakeGuild(recorder, channels={42: channel})
    bot_registry.set_bot(_FakeBot(guild))

    await dispatcher._execute_actions(1, [{"type": "create_thread", "params": {"channel_id": 42, "name": "討論串"}}])

    assert recorder.calls == [
        (
            "create_thread",
            42,
            {"name": "討論串", "type": dispatcher.discord.ChannelType.public_thread},
        )
    ]


async def test_archive_thread():
    recorder = _Recorder()
    thread = _FakeThread(recorder, 777)
    guild = _FakeGuild(recorder, threads={777: thread})
    bot_registry.set_bot(_FakeBot(guild))

    await dispatcher._execute_actions(1, [{"type": "archive_thread", "params": {"thread_id": 777}}])

    assert recorder.calls == [("edit_thread", 777, {"archived": True})]


async def test_one_failing_action_does_not_block_the_rest():
    """
    其中一個動作失敗（例如頻道已經被刪除、fetch_message 失敗）時，
    其餘動作仍然要繼續執行，不能整批中止。
    """
    recorder = _Recorder()
    broken_channel = _FakeChannel(recorder, 42, fail_fetch=True)
    working_channel = _FakeChannel(recorder, 43)
    guild = _FakeGuild(recorder, channels={42: broken_channel, 43: working_channel})
    bot_registry.set_bot(_FakeBot(guild))

    await dispatcher._execute_actions(
        1,
        [
            {"type": "edit_message", "params": {"channel_id": 42, "message_id": 100, "content": "x"}},
            {"type": "send_message", "params": {"channel_id": 43, "content": "still works"}},
        ],
    )

    assert recorder.calls == [("send", 43, {"content": "still works", "embed": None, "view": None})]


async def test_unknown_action_type_is_skipped_not_raised():
    recorder = _Recorder()
    guild = _FakeGuild(recorder)
    bot_registry.set_bot(_FakeBot(guild))

    await dispatcher._execute_actions(1, [{"type": "does_not_exist", "params": {}}])

    assert recorder.calls == []


async def test_missing_guild_is_handled_gracefully():
    bot_registry.set_bot(_FakeBot(_FakeGuild(_Recorder())))

    await dispatcher._execute_actions(999999, [{"type": "send_message", "params": {"channel_id": 1}}])
