import json

import pytest

from core.manifest import ManifestValidationError, parse_manifest

VALID_MANIFEST = {
    "name": "temp_role_punishment",
    "version": "1.0.0",
    "description": "管理員可指定成員與時長，時間到自動恢復原身分組",
    "capability_api_version": 1,
    "event_hooks": ["on_slash_command", "on_scheduled_task"],
    "required_capabilities": ["manage_roles", "schedule_task"],
    "slash_commands": [{"name": "temp_role", "description": "暫時調整成員身分組"}],
}


def test_parse_valid_manifest():
    manifest = parse_manifest(json.dumps(VALID_MANIFEST))
    assert manifest.name == "temp_role_punishment"
    assert manifest.event_hooks == ["on_slash_command", "on_scheduled_task"]
    assert manifest.slash_commands[0].name == "temp_role"


def test_invalid_json_raises():
    with pytest.raises(ManifestValidationError):
        parse_manifest("not valid json")


def test_unknown_event_hook_raises():
    manifest = {**VALID_MANIFEST, "event_hooks": ["on_something_undefined"]}
    with pytest.raises(ManifestValidationError):
        parse_manifest(json.dumps(manifest))


def test_unknown_capability_raises():
    manifest = {**VALID_MANIFEST, "required_capabilities": ["kick_member"]}
    with pytest.raises(ManifestValidationError):
        parse_manifest(json.dumps(manifest))


def test_unsupported_capability_api_version_raises():
    manifest = {**VALID_MANIFEST, "capability_api_version": 999}
    with pytest.raises(ManifestValidationError):
        parse_manifest(json.dumps(manifest))


def test_slash_command_hook_without_commands_raises():
    manifest = {**VALID_MANIFEST, "slash_commands": []}
    with pytest.raises(ManifestValidationError):
        parse_manifest(json.dumps(manifest))
