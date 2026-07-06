from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from features.verification import cog as verification_cog


class FakeMember:
    """提供驗證核准測試所需的最小成員介面。"""

    def __init__(self, roles: list[object], remove_error: Exception | None = None) -> None:
        self.id = 200
        self.roles = roles
        self.add_roles = AsyncMock()
        self.remove_roles = AsyncMock(side_effect=remove_error)


class FakeReviewMember:
    """提供審核頻道建立測試所需且可作為權限覆寫鍵值的成員介面。"""

    def __init__(self) -> None:
        self.id = 200
        self.name = "member"
        self.mention = "<@200>"
        self.created_at = verification_cog.datetime.datetime.now(verification_cog.datetime.timezone.utc)


@pytest.mark.asyncio
async def test_approve_member_does_not_update_status_when_role_change_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """移除待驗證身分組失敗時，應回復已新增身分組且不得寫入 approved。"""
    restricted_role = object()
    verified_role = object()
    member = FakeMember([restricted_role], remove_error=RuntimeError("remove failed"))
    guild = SimpleNamespace(
        id=100,
        get_role=lambda role_id: {
            1: restricted_role,
            2: verified_role,
        }.get(role_id),
    )
    set_status = AsyncMock()
    add_log_entry = AsyncMock()
    monkeypatch.setattr(verification_cog, "set_status", set_status)
    monkeypatch.setattr(verification_cog, "add_log_entry", add_log_entry)
    monkeypatch.setattr(verification_cog.i18n, "get_text", MagicMock(return_value="approved"))

    verification = object.__new__(verification_cog.Verification)
    result = await verification._approve_member(
        guild,
        member,
        {"restricted_role_id": 1, "verified_role_id": 2},
        "verification_auto_approved",
    )

    assert result is False
    member.add_roles.assert_awaited_once_with(verified_role, reason="approved")
    assert member.remove_roles.await_count == 2
    member.remove_roles.assert_any_await(restricted_role, reason="approved")
    member.remove_roles.assert_any_await(verified_role, reason="驗證核准失敗，回復已驗證身分組")
    set_status.assert_not_awaited()
    add_log_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_approve_member_updates_status_only_after_roles_succeed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """身分組操作完整成功後，才應更新狀態並新增稽核紀錄。"""
    restricted_role = object()
    verified_role = object()
    member = FakeMember([restricted_role])
    guild = SimpleNamespace(
        id=100,
        get_role=lambda role_id: {
            1: restricted_role,
            2: verified_role,
        }.get(role_id),
    )
    set_status = AsyncMock()
    add_log_entry = AsyncMock()
    monkeypatch.setattr(verification_cog, "set_status", set_status)
    monkeypatch.setattr(verification_cog, "add_log_entry", add_log_entry)
    monkeypatch.setattr(verification_cog.i18n, "get_text", MagicMock(return_value="approved"))

    verification = object.__new__(verification_cog.Verification)
    result = await verification._approve_member(
        guild,
        member,
        {"restricted_role_id": 1, "verified_role_id": 2},
        "verification_auto_approved",
    )

    assert result is True
    set_status.assert_awaited_once_with(100, 200, "approved")
    add_log_entry.assert_awaited_once_with(100, 200, "verification_auto_approved", "approved")


@pytest.mark.asyncio
async def test_open_review_channel_cleans_up_when_message_send_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """審核訊息發送失敗時，應刪除新頻道並把建立中狀態復原。"""
    review_channel = SimpleNamespace(
        id=300,
        send=AsyncMock(side_effect=RuntimeError("send failed")),
        delete=AsyncMock(),
    )
    default_role = object()
    bot_member = object()
    member = FakeReviewMember()
    guild = SimpleNamespace(
        id=100,
        categories=[SimpleNamespace(name=verification_cog.REVIEW_CATEGORY_NAME)],
        default_role=default_role,
        me=bot_member,
        get_role=lambda role_id: None,
        create_text_channel=AsyncMock(return_value=review_channel),
    )
    set_review_channel = AsyncMock(return_value=True)
    reset_review_creation = AsyncMock(return_value=True)
    complete_review_creation = AsyncMock(return_value=True)
    monkeypatch.setattr(verification_cog, "set_review_channel", set_review_channel)
    monkeypatch.setattr(verification_cog, "reset_review_creation", reset_review_creation)
    monkeypatch.setattr(verification_cog, "complete_review_creation", complete_review_creation)
    monkeypatch.setattr(verification_cog.i18n, "get_text", MagicMock(return_value="text"))

    verification = object.__new__(verification_cog.Verification)
    result = await verification._open_review_channel(guild, member, {})

    assert result is None
    review_channel.delete.assert_awaited_once()
    reset_review_creation.assert_awaited_once_with(100, 200)
    complete_review_creation.assert_not_awaited()
