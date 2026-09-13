import json
import os
import stat
from unittest.mock import Mock, patch

import pytest
import requests

from msf_assistant import cli
from msf_assistant.auth import TokenSet
from msf_assistant.token_store import TokenStoreError


@pytest.fixture
def environment(monkeypatch, tmp_path, settings):
    monkeypatch.setattr(cli.Settings, "from_env", lambda env_file: settings)
    monkeypatch.setattr(
        cli.Settings,
        "token_store_identity_from_env",
        lambda env_file: (settings.client_id, settings.oauth_base_url),
    )
    api = Mock()
    api.player_profile.return_value = {"data": {"name": "Test Commander"}}
    api.player_roster.return_value = {"data": [{"id": "TestCharacter"}]}
    api.inventory.return_value = {"data": [{"item": "gold", "quantity": 123}]}
    monkeypatch.setattr(cli, "MSFAPIClient", Mock(return_value=api))
    token = TokenSet("private-access", refresh_token="private-refresh")
    login = Mock(return_value=token)
    monkeypatch.setattr(cli, "browser_login", login)
    store = Mock()
    store.load.return_value = token
    factory = Mock(return_value=store)
    monkeypatch.setattr(cli, "KeychainTokenStore", factory)
    oauth = Mock()
    oauth.refresh.return_value = TokenSet("new-access", refresh_token=None)
    monkeypatch.setattr(cli, "MSFOAuth2", Mock(return_value=oauth))
    return api, login, store, factory, oauth, tmp_path / "outputs" / "snapshot.json"


def test_login_saves_tokens_and_private_snapshot(environment, capsys):
    api, login, store, factory, oauth, output = environment
    assert cli.main(["login", "--no-browser", "--output", str(output)]) == 0
    assert login.call_args.kwargs["open_browser"] is False
    assert store.save.call_args.args[0].access_token == "private-access"
    payload = json.loads(output.read_text())
    assert payload["roster"] == api.player_roster.return_value
    assert payload["profile"] == api.player_profile.return_value
    assert payload["inventory"] == api.inventory.return_value
    assert "retrieved_at" in payload
    assert "private-access" not in output.read_text()
    assert "private-refresh" not in output.read_text()
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    messages = capsys.readouterr()
    assert "private-access" not in messages.out + messages.err
    assert "private-refresh" not in messages.out + messages.err
    oauth.refresh.assert_not_called()


def test_no_save_login_never_opens_keychain(environment):
    _, _, _, factory, _, output = environment
    assert cli.main(["login", "--no-save", "--output", str(output)]) == 0
    factory.assert_not_called()
    assert output.is_file()


def test_sync_refreshes_and_preserves_refresh_token(environment):
    _, login, store, _, oauth, output = environment
    assert cli.main(["sync", "--output", str(output)]) == 0
    oauth.refresh.assert_called_once_with("private-refresh")
    saved = store.save.call_args.args[0]
    assert saved.access_token == "new-access"
    assert saved.refresh_token == "private-refresh"
    assert cli.MSFAPIClient.call_args.args[1] == "new-access"
    login.assert_not_called()


def test_api_failure_preserves_previous_snapshot(environment, capsys):
    api, _, _, _, _, output = environment
    output.parent.mkdir()
    output.write_text("previous snapshot")
    api.player_roster.side_effect = requests.HTTPError("private-access-in-error")
    assert cli.main(["sync", "--output", str(output)]) == 1
    assert output.read_text() == "previous snapshot"
    assert "private-access-in-error" not in capsys.readouterr().err


def test_missing_saved_login_is_actionable(environment, capsys):
    _, _, store, _, _, output = environment
    store.load.return_value = None
    assert cli.main(["sync", "--output", str(output)]) == 1
    assert "login" in capsys.readouterr().err
    assert not output.exists()


def test_storage_failure_does_not_fall_back_to_plaintext(environment):
    _, _, store, _, _, output = environment
    store.save.side_effect = TokenStoreError("Keychain unavailable")
    assert cli.main(["login", "--output", str(output)]) == 1
    assert not output.exists()


def test_logout_deletes_only_local_tokens(environment):
    _, login, store, _, oauth, output = environment
    assert cli.main(["logout"]) == 0
    store.clear.assert_called_once()
    login.assert_not_called()
    oauth.refresh.assert_not_called()


def test_logout_ignores_server_credentials_and_timeout(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("MSF_CLIENT_ID=dotenv-client\nMSF_REQUEST_TIMEOUT=not-a-number\n")
    store = Mock()
    factory = Mock(return_value=store)
    monkeypatch.setattr(cli, "KeychainTokenStore", factory)
    monkeypatch.setattr(cli, "browser_login", Mock())
    monkeypatch.setattr(cli.requests, "Session", Mock())

    with patch.dict(os.environ, {"MSF_CLIENT_ID": "logout-client"}, clear=True):
        result = cli.main(["--env-file", str(env_file), "logout"])

    assert result == 0
    factory.assert_called_once_with(
        "logout-client", "https://hydra-public.prod.m3.scopelypv.com/oauth2"
    )
    store.clear.assert_called_once_with()
    cli.browser_login.assert_not_called()
    cli.requests.Session.assert_not_called()


def test_refresh_failure_does_not_overwrite_store(environment, capsys):
    _, _, store, _, oauth, output = environment
    oauth.refresh.side_effect = requests.HTTPError("refresh-secret")
    assert cli.main(["sync", "--output", str(output)]) == 1
    store.save.assert_not_called()
    assert not output.exists()
    assert "refresh-secret" not in capsys.readouterr().err


def test_failed_write_leaves_no_partial_file(monkeypatch, tmp_path):
    output = tmp_path / "snapshot.json"
    output.write_text("previous snapshot")
    monkeypatch.setattr(cli.os, "replace", Mock(side_effect=OSError("disk problem")))
    with pytest.raises(OSError):
        cli.write_snapshot(output, {"profile": {}})
    assert output.read_text() == "previous snapshot"
    assert list(tmp_path.iterdir()) == [output]


def test_sync_can_include_character_catalogue(environment):
    api, _, _, _, _, output = environment
    api.game_characters.return_value = [{"id": "TestCharacter", "name": "Test Character"}]
    assert cli.main(["sync", "--characters", "--output", str(output)]) == 0
    payload = json.loads(output.read_text())
    assert payload["characters"] == api.game_characters.return_value
    assert payload["characters_retrieved_at"]


def test_character_failure_preserves_snapshot(environment):
    api, _, _, _, _, output = environment
    output.parent.mkdir()
    output.write_text("previous")
    api.game_characters.side_effect = requests.HTTPError("private")
    assert cli.main(["sync", "--characters", "--output", str(output)]) == 1
    assert output.read_text() == "previous"


def test_malformed_success_response_preserves_previous_snapshot(environment, capsys):
    api, _, _, _, _, output = environment
    output.parent.mkdir()
    previous = b'{"valid": "previous snapshot"}\n'
    output.write_bytes(previous)
    api.player_roster.return_value = {"data": [{"power": 100}]}

    assert cli.main(["sync", "--output", str(output)]) == 1

    assert output.read_bytes() == previous
    assert "malformed" not in capsys.readouterr().err.lower()


def test_ordinary_sync_preserves_valid_previous_catalogue(environment):
    _, _, _, _, _, output = environment
    output.parent.mkdir()
    old_catalogue_time = "2026-09-10T12:00:00+00:00"
    output.write_text(
        json.dumps(
            {
                "profile": {"data": {"name": "Old"}},
                "roster": {"data": [{"id": "TestCharacter"}]},
                "inventory": {"data": []},
                "retrieved_at": "2026-09-11T12:00:00+00:00",
                "characters": [{"id": "TestCharacter", "name": "Test Character"}],
                "characters_retrieved_at": old_catalogue_time,
            }
        )
    )

    assert cli.main(["sync", "--output", str(output)]) == 0

    payload = json.loads(output.read_text())
    assert payload["characters"] == [{"id": "TestCharacter", "name": "Test Character"}]
    assert payload["characters_retrieved_at"] == old_catalogue_time


def test_ordinary_sync_ignores_malformed_previous_catalogue(environment):
    _, _, _, _, _, output = environment
    output.parent.mkdir()
    output.write_text(
        json.dumps(
            {
                "characters": [{"name": "Missing ID"}],
                "characters_retrieved_at": "not-a-timestamp",
            }
        )
    )

    assert cli.main(["sync", "--output", str(output)]) == 0

    payload = json.loads(output.read_text())
    assert "characters" not in payload
    assert "characters_retrieved_at" not in payload


def test_status_and_mcp_config_need_no_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.Settings, "from_env", Mock(side_effect=AssertionError("no secrets")))
    assert cli.main(["status", "--snapshot", str(tmp_path / "missing")]) == 0
    assert json.loads(capsys.readouterr().out)["available"] is False
    assert cli.main(["mcp-config", "--snapshot", str(tmp_path / "missing")]) == 0
    config = json.loads(capsys.readouterr().out)["mcpServers"]["msf-assistant"]
    assert os.path.isabs(config["command"])
    assert "serve" in config["args"]
    assert "secret" not in str(config)


def test_operation_lock_rejects_concurrent_writer(tmp_path):
    from msf_assistant.operation_lock import operation_lock

    with (
        operation_lock(tmp_path / ".env"),
        pytest.raises(cli.SyncError, match="läuft bereits"),
        operation_lock(tmp_path / ".env"),
    ):
        pytest.fail("Second writer entered")
