import pytest
import requests

from msf_assistant.client import MSFAPIClient, MSFAPIError


@pytest.fixture
def client(settings, session) -> MSFAPIClient:
    return MSFAPIClient(settings, "token", session)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("player_profile", "player/v1/card"),
        ("player_roster", "player/v1/roster"),
        ("inventory", "player/v1/inventory"),
    ],
)
def test_player_resources_use_expected_endpoint(client, session, method, path) -> None:
    session.get.return_value.json.return_value = {"data": {"ok": True}}
    assert getattr(client, method)() == {"data": {"ok": True}}
    assert session.get.call_args.args[0].endswith(path)


def test_client_configures_authentication_headers(settings, session) -> None:
    MSFAPIClient(settings, "secret-token", session)
    assert session.headers["Authorization"] == "Bearer secret-token"
    assert session.headers["x-api-key"] == "api-key"
    assert session.headers["User-Agent"] == "APIClient/1.0 (Server)"
    assert settings.client_secret not in str(session.headers)


def test_characters_are_paginated(client, session) -> None:
    first_page = [{"id": "one"}, {"id": "two"}]
    session.get.return_value.json.side_effect = [{"data": first_page}, {"data": [{"id": "three"}]}]
    assert client.game_characters(per_page=2) == [*first_page, {"id": "three"}]
    assert session.get.call_count == 2
    assert session.get.call_args_list[1].kwargs["params"]["page"] == 2


def test_invalid_json_is_wrapped(client, session) -> None:
    session.get.return_value.json.side_effect = requests.exceptions.JSONDecodeError(
        "bad json", "x", 0
    )
    with pytest.raises(MSFAPIError, match="invalid JSON"):
        client.player_roster()


def test_http_errors_are_not_hidden(client, session) -> None:
    session.get.return_value.raise_for_status.side_effect = requests.HTTPError("401")
    with pytest.raises(requests.HTTPError):
        client.inventory()
