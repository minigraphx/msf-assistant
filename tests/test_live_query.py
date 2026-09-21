"""One authenticated MSF query with a single token refresh on 401."""

from unittest.mock import Mock

import pytest
import requests

from msf_assistant.auth import TokenSet
from msf_assistant.live_query import CredentialsRejected, RefreshFailed, run_query


def http_error(status):
    response = Mock(status_code=status)
    return requests.HTTPError(response=response)


def test_query_uses_the_stored_access_token_without_refreshing(settings, monkeypatch):
    from msf_assistant import live_query

    monkeypatch.setattr(
        live_query.MSFOAuth2, "refresh", lambda *a, **k: pytest.fail("must not refresh")
    )
    saved = []
    seen = []

    def fn(client):
        seen.append(client.session.headers["Authorization"])
        return {"ok": True}

    tokens = TokenSet("access", refresh_token="refresh")
    assert run_query(settings, tokens, fn, save_tokens=saved.append) == {"ok": True}
    assert seen == ["Bearer access"] and saved == []


def test_query_refreshes_once_after_401_and_persists_before_retrying(settings, monkeypatch):
    from msf_assistant import live_query

    monkeypatch.setattr(
        live_query.MSFOAuth2, "refresh", lambda self, token: TokenSet("fresh", expires_in=3600)
    )
    saved = []
    calls = []

    def fn(client):
        calls.append(client.session.headers["Authorization"])
        if len(calls) == 1:
            raise http_error(401)
        assert saved and saved[0].access_token == "fresh"
        return {"ok": True}

    tokens = TokenSet("stale", refresh_token="keep-me")
    assert run_query(settings, tokens, fn, save_tokens=saved.append) == {"ok": True}
    assert calls == ["Bearer stale", "Bearer fresh"]
    # A refresh response without a new refresh token keeps the old one.
    assert saved[0].refresh_token == "keep-me"


def test_query_reports_rejected_credentials_distinctly(settings, monkeypatch):
    from msf_assistant import live_query

    monkeypatch.setattr(live_query.MSFOAuth2, "refresh", lambda self, token: TokenSet("fresh"))

    def always_401(client):
        raise http_error(401)

    # Still 401 with a fresh token, or no refresh token at all: the sign-in is gone.
    with pytest.raises(CredentialsRejected):
        run_query(settings, TokenSet("a", refresh_token="r"), always_401, save_tokens=Mock())
    with pytest.raises(CredentialsRejected):
        run_query(settings, TokenSet("a"), always_401, save_tokens=Mock())

    def refresh_rejected(self, token):
        raise http_error(400)

    monkeypatch.setattr(live_query.MSFOAuth2, "refresh", refresh_rejected)
    with pytest.raises(CredentialsRejected):
        run_query(settings, TokenSet("a", refresh_token="r"), always_401, save_tokens=Mock())


def test_query_does_not_refresh_on_other_http_errors(settings, monkeypatch):
    from msf_assistant import live_query

    monkeypatch.setattr(
        live_query.MSFOAuth2, "refresh", lambda *a, **k: pytest.fail("must not refresh")
    )

    for status in (400, 403, 404, 500):

        def failing(client, status=status):
            raise http_error(status)

        with pytest.raises(requests.HTTPError) as failure:
            run_query(settings, TokenSet("a", refresh_token="r"), failing, save_tokens=Mock())
        assert not isinstance(failure.value, CredentialsRejected)


def test_refresh_uses_its_own_session_and_reports_transport_failures(settings, monkeypatch):
    from msf_assistant import live_query

    sessions = []

    def refresh(self, token):
        sessions.append(self.session)
        raise http_error(404)

    monkeypatch.setattr(live_query.MSFOAuth2, "refresh", refresh)
    api_sessions = []

    def fn(client):
        api_sessions.append(client.session)
        raise http_error(401)

    with pytest.raises(RefreshFailed):
        run_query(settings, TokenSet("a", refresh_token="r"), fn, save_tokens=Mock())
    # The API session carries Bearer/x-api-key headers; the token endpoint must not see them.
    assert sessions and sessions[0] is not api_sessions[0]
    assert "x-api-key" not in sessions[0].headers


def test_query_bounds_the_response_budget(settings):
    def fn(client):
        return client.remaining_bytes

    assert run_query(settings, TokenSet("a"), fn, save_tokens=Mock(), max_bytes=1234) == 1234
