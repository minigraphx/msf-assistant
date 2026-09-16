"""Hosted configuration must fail closed without touching local credentials."""

import os
import subprocess
import sys

import pytest
from cryptography.fernet import Fernet


def config_env(tmp_path):
    key = tmp_path / "key"
    secret = tmp_path / "secret"
    for file, contents in ((key, Fernet.generate_key()), (secret, b"synthetic")):
        file.write_bytes(contents)
        file.chmod(0o600)
    return {
        "MSF_HOSTED_DATA": str(tmp_path / "data"),
        "MSF_HOSTED_MOUNT": str(tmp_path),
        "MSF_HOSTED_KEY_FILE": str(key),
        "MSF_CLIENT_SECRET_FILE": str(secret),
        "MSF_CLIENT_ID": "synthetic",
        "MSF_PUBLIC_URL": "https://msf.example",
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://msf.example",
        "https://msf.example/path",
        "https://user@msf.example",
        "https://msf.example?x=1",
        "https://msf.example/#bad",
    ],
)
def test_invalid_public_origin(tmp_path, url):
    from msf_assistant.hosted_cli import HostedConfig

    env = config_env(tmp_path)
    env["MSF_PUBLIC_URL"] = url
    with pytest.raises(ValueError):
        HostedConfig.from_env(env)


def test_wrong_mount_and_unsafe_secret(tmp_path, monkeypatch):
    from msf_assistant.hosted_cli import HostedConfig

    env = config_env(tmp_path)
    with pytest.raises(ValueError, match="mount"):
        HostedConfig.from_env(env)
    monkeypatch.setattr(os.path, "ismount", lambda p: True)
    config = HostedConfig.from_env(env)
    assert config.settings.redirect_uri == "https://msf.example/oauth/callback"
    assert config.settings.client_secret == "synthetic"
    (tmp_path / "secret").chmod(0o644)
    with pytest.raises(ValueError, match="secret"):
        HostedConfig.from_env(env)
    (tmp_path / "secret").unlink()
    with pytest.raises(ValueError, match="secret"):
        HostedConfig.from_env(env)


def test_generate_key_is_exclusive_private_and_never_printed(tmp_path):
    path = tmp_path / "generated.key"
    command = [sys.executable, "-m", "msf_assistant", "hosted", "generate-key", str(path)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    key = path.read_bytes()
    Fernet(key)
    assert key.decode() not in result.stdout + result.stderr
    assert path.stat().st_mode & 0o777 == 0o600
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode != 0
    assert path.read_bytes() == key


def test_local_help_does_not_import_hosted_dependencies():
    code = 'import sys; from msf_assistant.cli import main; main(["--help"])'
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0


def test_serve_refuses_restore_marker_and_forces_one_worker(tmp_path, monkeypatch):
    from msf_assistant.hosted_cli import HostedConfig, serve
    from msf_assistant.hosted_store import HostedStore

    env = config_env(tmp_path)
    monkeypatch.setattr(os.path, "ismount", lambda p: True)
    config = HostedConfig.from_env(env)
    store = HostedStore(config.root, config.key)
    marker = store.root / "RESTORE_MAINTENANCE"
    marker.touch()
    with pytest.raises(ValueError, match="maintenance"):
        serve(config)
    marker.unlink()
    calls = []
    monkeypatch.setenv("WEB_CONCURRENCY", "8")
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: calls.append(kwargs))
    serve(config)
    assert calls[0]["workers"] == 1
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["access_log"] is False


def test_wrong_issuer_and_unavailable_key(tmp_path, monkeypatch):
    from msf_assistant.hosted_cli import HostedConfig

    env = config_env(tmp_path)
    monkeypatch.setattr(os.path, "ismount", lambda p: True)
    env["MSF_OAUTH_BASE_URL"] = "https://attacker.example"
    with pytest.raises(ValueError, match="issuer"):
        HostedConfig.from_env(env)
    del env["MSF_OAUTH_BASE_URL"]
    (tmp_path / "key").unlink()
    with pytest.raises(ValueError, match="secret"):
        HostedConfig.from_env(env)


def test_admission_configuration_is_bounded(tmp_path, monkeypatch):
    from msf_assistant.hosted_cli import HostedConfig

    env = config_env(tmp_path)
    monkeypatch.setattr(os.path, "ismount", lambda p: True)
    assert HostedConfig.from_env(env).active_requests == 2
    for value in ("0", "5", "-1", "no"):
        env["MSF_HOSTED_ACTIVE_REQUESTS"] = value
        with pytest.raises(ValueError):
            HostedConfig.from_env(env)


def test_mount_cannot_be_root_or_escaped_and_keys_stay_outside_state(tmp_path, monkeypatch):
    from msf_assistant.hosted_cli import HostedConfig

    env = config_env(tmp_path)
    monkeypatch.setattr(os.path, "ismount", lambda p: True)
    env["MSF_HOSTED_MOUNT"] = "/"
    with pytest.raises(ValueError, match="mount"):
        HostedConfig.from_env(env)
    env["MSF_HOSTED_MOUNT"] = str(tmp_path)
    env["MSF_HOSTED_DATA"] = str(tmp_path / ".." / "escaped")
    with pytest.raises(ValueError, match="mount"):
        HostedConfig.from_env(env)
    env["MSF_HOSTED_DATA"] = str(tmp_path)
    env["MSF_HOSTED_MOUNT"] = str(tmp_path.parent)
    with pytest.raises(ValueError, match="outside"):
        HostedConfig.from_env(env)
