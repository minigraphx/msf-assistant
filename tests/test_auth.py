from base64 import b64encode
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from msf_assistant.auth import MSFOAuth2, OAuthStateError
from msf_assistant.config import Settings


def test_authorization_url_contains_encoded_parameters(settings, session) -> None:
    url, state = MSFOAuth2(settings, session).authorization_url(state="known-state")
    query = parse_qs(urlparse(url).query)
    assert state == "known-state"
    assert query["client_id"] == ["client-id"]
    assert query["redirect_uri"] == ["http://localhost/callback"]
    assert "m3p.f.pr.ros" in query["scope"][0]
    assert "m3p.f.pr.inv" in query["scope"][0]


def test_parse_callback_validates_state() -> None:
    assert (
        MSFOAuth2.parse_callback(
            "http://localhost/callback?code=abc&state=expected", expected_state="expected"
        )
        == "abc"
    )
    with pytest.raises(OAuthStateError):
        MSFOAuth2.parse_callback(
            "http://localhost/callback?code=abc&state=wrong", expected_state="expected"
        )


def test_exchange_code_returns_typed_tokens(settings, session) -> None:
    response = session.post.return_value
    response.json.return_value = {
        "access_token": "access",
        "refresh_token": "refresh",
        "expires_in": 3600,
    }
    token = MSFOAuth2(settings, session).exchange_code("auth-code")
    assert token.access_token == "access"
    assert token.refresh_token == "refresh"
    assert session.post.call_args.kwargs["data"]["grant_type"] == "authorization_code"
    response.raise_for_status.assert_called_once()


def test_refresh_uses_refresh_grant(settings, session) -> None:
    session.post.return_value.json.return_value = {"access_token": "new"}
    MSFOAuth2(settings, session).refresh("old-refresh")
    assert session.post.call_args.kwargs["data"]["grant_type"] == "refresh_token"


@pytest.mark.parametrize(
    ("operation", "grant", "credential_field"),
    [
        ("exchange_code", "authorization_code", "code"),
        ("refresh", "refresh_token", "refresh_token"),
    ],
)
def test_token_requests_use_oauth_basic_auth(
    monkeypatch, operation, grant, credential_field
) -> None:
    settings = Settings(
        client_id="client:id", api_key="public-key", client_secret="secret+with spaces/"
    )
    captured = []

    def capture_send(request, **kwargs):
        captured.append((request, kwargs))
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"access_token":"access","refresh_token":"refresh"}'
        return response

    with requests.Session() as session:
        session.trust_env = False
        monkeypatch.setattr(session, "send", capture_send)
        oauth = MSFOAuth2(settings, session)
        url, _ = oauth.authorization_url()
        tokens = getattr(oauth, operation)("one-time-value")

    request, kwargs = captured[0]
    assert request.url == "https://hydra-public.prod.m3.scopelypv.com/oauth2/token"
    encoded = b64encode(b"client%3Aid:secret%2Bwith+spaces%2F").decode("ascii")
    assert request.headers["Authorization"] == f"Basic {encoded}"
    assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
    payload = parse_qs(request.body)
    assert payload["grant_type"] == [grant]
    assert payload[credential_field] == ["one-time-value"]
    if operation == "exchange_code":
        assert payload["redirect_uri"] == [settings.redirect_uri]
    assert "client_secret" not in payload
    assert "client_id" not in payload
    assert "client_secret" not in parse_qs(urlparse(url).query)
    assert settings.client_secret not in url
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"] == settings.request_timeout
    assert tokens.access_token == "access"


@pytest.mark.parametrize("operation", ["exchange_code", "refresh"])
def test_token_request_requires_secret_before_network_call(session, operation) -> None:
    settings = Settings(client_id="client", api_key="public-key")

    with pytest.raises(ValueError, match="MSF_CLIENT_SECRET"):
        getattr(MSFOAuth2(settings, session), operation)("value")

    session.post.assert_not_called()


def test_token_http_errors_are_preserved(settings, session) -> None:
    session.post.return_value.raise_for_status.side_effect = requests.HTTPError("401")
    with pytest.raises(requests.HTTPError, match="401"):
        MSFOAuth2(settings, session).exchange_code("code")
