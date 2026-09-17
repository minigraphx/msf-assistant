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
        "MSF_OPERATOR_NAME": "Synthetic Operator",
        "MSF_OPERATOR_ADDRESS": "Somewhere 1, 8000 Zürich",
        "MSF_OPERATOR_EMAIL": "operator@example.invalid",
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://msf.example",
        "https://msf.example/path",
        "https://user@msf.example",
        "https://msf.example?x=1",
        "https://msf.example/#bad",
        "https://msf.example:443",
        "https://msf.example:8443",
    ],
)
def test_invalid_public_origin(tmp_path, url):
    from msf_assistant.hosted_cli import HostedConfig

    env = config_env(tmp_path)
    env["MSF_PUBLIC_URL"] = url
    with pytest.raises(ValueError, match="HTTPS origin"):
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
    code = (
        "import sys; from msf_assistant.cli import main\n"
        "try:\n    main(['--help'])\nexcept SystemExit:\n    pass\n"
        "loaded = [m for m in ('cryptography', 'mcp', 'uvicorn', 'starlette',"
        " 'msf_assistant.hosted_cli') if m in sys.modules]\n"
        "print('LOADED=' + ','.join(loaded))"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "LOADED=\n" in result.stdout


def test_hosted_without_extra_reports_missing_package_instead_of_traceback():
    code = (
        "import sys; sys.modules['cryptography'] = None\n"
        "from msf_assistant.cli import main\n"
        "sys.exit(main(['hosted', '--help']))"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "hosted" in result.stderr and "pip install" in result.stderr


def test_main_dispatches_backup_restore_and_resume(tmp_path, monkeypatch, capsys):
    from msf_assistant.hosted_cli import main
    from msf_assistant.hosted_store import HostedStore

    env = config_env(tmp_path)
    monkeypatch.setattr(os.path, "ismount", lambda p: True)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    HostedStore(tmp_path / "data", (tmp_path / "key").read_bytes()).player("issuer", "alice")

    assert main(["backup", str(tmp_path / "backups")]) == 0
    archive = capsys.readouterr().out.strip()
    assert archive.startswith(str(tmp_path / "backups")) and os.path.exists(archive)

    monkeypatch.setenv("MSF_HOSTED_DATA", str(tmp_path / "restored"))
    assert main(["restore", archive, "--maintenance"]) == 0
    assert "maintenance" in capsys.readouterr().out.lower()
    assert (tmp_path / "restored" / "RESTORE_MAINTENANCE").exists()
    # A second restore into the same root must be refused without touching it.
    assert main(["restore", archive, "--maintenance"]) == 1
    assert "must not exist" in capsys.readouterr().err
    assert (tmp_path / "restored" / "RESTORE_MAINTENANCE").exists()

    assert main(["resume", "--deletions-reconciled"]) == 0
    assert not (tmp_path / "restored" / "RESTORE_MAINTENANCE").exists()
    assert main(["backup", str(tmp_path / "restored" / "inside")]) == 1
    assert "outside the data root" in capsys.readouterr().err


def test_main_reports_corrupt_archive_without_details(tmp_path, monkeypatch, capsys):
    from msf_assistant.hosted_cli import main

    env = config_env(tmp_path)
    monkeypatch.setattr(os.path, "ismount", lambda p: True)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    bogus = tmp_path / "bogus.tar"
    bogus.write_bytes(b"not a tar archive")
    assert main(["restore", str(bogus), "--maintenance"]) == 1
    err = capsys.readouterr().err
    assert err.strip() == "Hosted operation failed: invalid backup or database"
    assert not (tmp_path / "data").exists()


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


def test_operator_identity_is_required_for_public_pages(tmp_path, monkeypatch):
    from msf_assistant.hosted_cli import HostedConfig

    env = config_env(tmp_path)
    monkeypatch.setattr(os.path, "ismount", lambda p: True)
    config = HostedConfig.from_env(env)
    assert config.operator.name == "Synthetic Operator"
    assert config.operator.email == "operator@example.invalid"
    for key, bad in (
        ("MSF_OPERATOR_NAME", ""),
        ("MSF_OPERATOR_ADDRESS", "   "),
        ("MSF_OPERATOR_EMAIL", "not-an-address"),
    ):
        broken = dict(env, **{key: bad})
        with pytest.raises(ValueError, match="operator"):
            HostedConfig.from_env(broken)
    del env["MSF_OPERATOR_NAME"]
    with pytest.raises(ValueError, match="operator"):
        HostedConfig.from_env(env)


def test_delete_player_command_removes_data_and_grants(tmp_path, monkeypatch, capsys):
    from test_hosted_oauth import run, tokens_for

    from msf_assistant.hosted_cli import main
    from msf_assistant.hosted_oauth import HostedOAuthProvider
    from msf_assistant.hosted_store import HostedStore, HostedStoreError

    env = config_env(tmp_path)
    monkeypatch.setattr(os.path, "ismount", lambda p: True)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    store = HostedStore(tmp_path / "data", (tmp_path / "key").read_bytes())
    provider = HostedOAuthProvider(store, "https://msf.example")
    player, tokens, _ = tokens_for(provider, store)
    directory = store.player_dir(player.id)
    (directory / "snapshot.json").write_text("{}")

    assert main(["delete-player", player.id]) == 0
    assert "deleted" in capsys.readouterr().out.lower()
    assert not directory.exists()
    assert (
        run(
            HostedOAuthProvider(store, "https://msf.example").load_access_token(tokens.access_token)
        )
        is None
    )
    with pytest.raises(HostedStoreError):
        store.require_player(player.id)
    assert main(["delete-player", player.id]) == 1
    assert main(["delete-player", "not-a-uuid"]) == 1
    assert "credential" not in capsys.readouterr().err.lower()


def test_hosted_after_local_options_gets_a_hint_not_a_local_error(capsys):
    from msf_assistant.cli import main

    assert main(["--env-file", "x", "hosted", "serve"]) == 2
    assert "erste Argument" in capsys.readouterr().err
