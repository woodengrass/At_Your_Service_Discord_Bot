import logging

from pytest import LogCaptureFixture

from hubs.server_setting.panel import format_config_embed


def test_format_config_embed_has_link_checker_setting_labels(caplog: LogCaptureFixture) -> None:
    """
    設定總覽顯示連結檢查設定時，不應因 enabled 等設定鍵缺翻譯而記錄錯誤。
    """
    data = {
        "common": {},
        "modules": {
            "link_checker": {
                "enabled": True,
                "qr_code_enabled": True,
                "image_hash_enabled": True,
            }
        },
    }

    with caplog.at_level(logging.ERROR, logger="core.i18n"):
        embed = format_config_embed(123, data)

    assert "labels.enabled" not in embed.fields[0].value
    assert not caplog.records
