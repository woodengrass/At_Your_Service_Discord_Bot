from types import SimpleNamespace

import pytest

from features.anti_spam import cog as anti_spam_cog
from features.anti_spam.cog import AntiSpam


def _make_message(channel_id: int) -> SimpleNamespace:
    """
    建立防洗版測試所需的最小訊息物件。

    Args:
        channel_id: 訊息所在頻道 ID

    Returns:
        SimpleNamespace，模擬 Discord 訊息
    """
    return SimpleNamespace(
        content="repeat",
        author=SimpleNamespace(id=200, bot=False),
        guild=SimpleNamespace(id=100),
        channel=SimpleNamespace(id=channel_id),
    )


@pytest.mark.asyncio
async def test_allowed_channel_is_not_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    允許頻道中的訊息不應進入防洗版歷史紀錄。
    """
    settings = SimpleNamespace(
        get_module_config=lambda guild_id, module_name: {"enabled": True, "allowed_channel_ids": ["300"]},
        get_whitelist=lambda guild_id: [],
    )
    monkeypatch.setattr(anti_spam_cog, "GuildSettings", settings)
    cog = object.__new__(AntiSpam)
    cog.message_history = {}

    await cog.on_message(_make_message(300))

    assert cog.message_history == {}


@pytest.mark.asyncio
async def test_non_allowed_channel_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    非允許頻道仍應依原本流程被防洗版歷史紀錄追蹤。
    """
    settings = SimpleNamespace(
        get_module_config=lambda guild_id, module_name: {"enabled": True, "allowed_channel_ids": ["300"]},
        get_whitelist=lambda guild_id: [],
    )
    monkeypatch.setattr(anti_spam_cog, "GuildSettings", settings)
    cog = object.__new__(AntiSpam)
    cog.message_history = {}

    await cog.on_message(_make_message(301))

    assert (100, 200) in cog.message_history
    assert len(cog.message_history[(100, 200)]) == 1
