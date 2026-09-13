import pytest

from msf_assistant.config import Settings


def test_settings_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("MSF_CLIENT_ID", " client ")
    monkeypatch.setenv("MSF_API_KEY", " key ")
    monkeypatch.setenv("MSF_REQUEST_TIMEOUT", "4.5")

    settings = Settings.from_env(env_file=None)

    assert settings.client_id == "client"
    assert settings.api_key == "key"
    assert settings.request_timeout == 4.5


def test_settings_reports_missing_secrets(monkeypatch) -> None:
    monkeypatch.delenv("MSF_CLIENT_ID", raising=False)
    monkeypatch.delenv("MSF_API_KEY", raising=False)

    with pytest.raises(ValueError, match="MSF_CLIENT_ID, MSF_API_KEY"):
        Settings.from_env(env_file=None)
