from urllib.parse import parse_qs, urlparse

import pytest

from msf_assistant.auth import MSFOAuth2, OAuthStateError


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
