from unittest.mock import patch

import pytest

from msf_assistant.config import Settings


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch) -> None:
    for name in (
        "MSF_CLIENT_ID", "MSF_CLIENT_SECRET", "MSF_API_KEY", "MSF_REDIRECT_URI",
        "MSF_REQUEST_TIMEOUT", "MSF_API_BASE_URL", "MSF_OAUTH_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_settings_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("MSF_CLIENT_ID", " client ")
    monkeypatch.setenv("MSF_CLIENT_SECRET", "private-secret")
    monkeypatch.setenv("MSF_API_KEY", " key ")
    monkeypatch.setenv("MSF_REQUEST_TIMEOUT", "4.5")

    settings = Settings.from_env(env_file=None)

    assert settings.client_id == "client"
    assert settings.client_secret == "private-secret"
    assert settings.api_key == "key"
    assert settings.request_timeout == 4.5


def test_settings_reports_missing_secrets(monkeypatch) -> None:
    monkeypatch.delenv("MSF_CLIENT_ID", raising=False)
    monkeypatch.delenv("MSF_API_KEY", raising=False)

    with pytest.raises(ValueError, match="MSF_CLIENT_ID, MSF_CLIENT_SECRET"):
        Settings.from_env(env_file=None)


@pytest.mark.parametrize("api_key", [None, "", "  "])
def test_id_and_secret_use_documented_public_api_key(monkeypatch, api_key) -> None:
    monkeypatch.setenv("MSF_CLIENT_ID", "client")
    monkeypatch.setenv("MSF_CLIENT_SECRET", "private-secret")
    if api_key is not None:
        monkeypatch.setenv("MSF_API_KEY", api_key)

    settings = Settings.from_env(env_file=None)

    # Public shared value from the official MSF specification, not a personal key.
    assert settings.api_key == "17wMKJLRxy3pYDCKG5ciP7VSU45OVumB2biCzzgw"
    assert settings.client_secret == "private-secret"
    assert "private-secret" not in repr(settings)


@pytest.mark.parametrize("secret", [None, "", "  "])
def test_server_configuration_requires_client_secret(monkeypatch, secret) -> None:
    monkeypatch.setenv("MSF_CLIENT_ID", "client")
    monkeypatch.setenv("MSF_API_KEY", "override-key")
    if secret is not None:
        monkeypatch.setenv("MSF_CLIENT_SECRET", secret)

    with pytest.raises(ValueError, match="MSF_CLIENT_SECRET"):
        Settings.from_env(env_file=None)


def test_credentials_load_from_dotenv(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MSF_CLIENT_ID=local-client\n"
        "MSF_CLIENT_SECRET=local-secret\n"
        "MSF_REDIRECT_URI=http://localhost:8000/oauth/callback\n"
    )

    with patch.dict("os.environ", {}, clear=True):
        settings = Settings.from_env(str(env_file))

    assert settings.client_id == "local-client"
    assert settings.client_secret == "local-secret"
    assert settings.redirect_uri == "http://localhost:8000/oauth/callback"
