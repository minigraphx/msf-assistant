import json
from unittest.mock import Mock

import pytest

from msf_assistant.config import Settings
from msf_assistant.hosted_identity import ISSUER, MSFIdentity


def settings(**kw):
    return Settings(client_id="app", client_secret="secret", **kw)


def test_reject_untrusted_config():
    with pytest.raises(ValueError):
        MSFIdentity(settings(oauth_base_url="https://evil.example/oauth2"))


@pytest.mark.parametrize("subject", [None, "", "   ", 123])
def test_userinfo_requires_subject(subject):
    identity = MSFIdentity(settings())
    identity.oauth.session = Mock()
    for method in (identity.oauth.session.get, identity.oauth.session.post):
        response = method.return_value
        response.iter_content.side_effect = lambda chunk_size, r=response: [
            json.dumps(r.json.return_value).encode()
        ]
    identity.oauth.session.get.return_value.status_code = 200
    identity.oauth.session.post.return_value.json.return_value = {"access_token": "access"}
    identity.oauth.session.get.return_value.json.return_value = {"sub": subject}
    with pytest.raises(ValueError):
        identity.exchange("code")


def test_verified_identity():
    identity = MSFIdentity(settings())
    identity.oauth.session = Mock()
    for method in (identity.oauth.session.get, identity.oauth.session.post):
        response = method.return_value
        response.iter_content.side_effect = lambda chunk_size, r=response: [
            json.dumps(r.json.return_value).encode()
        ]
    identity.oauth.session.get.return_value.status_code = 200
    identity.oauth.session.post.return_value.json.return_value = {"access_token": "access"}
    identity.oauth.session.get.return_value.json.return_value = {"sub": "alice"}
    issuer, subject, tokens = identity.exchange("code")
    assert (issuer, subject, tokens.access_token) == (ISSUER, "alice", "access")
    identity.oauth.session.get.assert_called_once_with(
        ISSUER + "userinfo",
        headers={"Authorization": "Bearer access"},
        timeout=30.0,
        allow_redirects=False,
        stream=True,
    )


@pytest.mark.parametrize("timeout", [float("inf"), float("nan"), 0, -1])
def test_timeout_must_be_finite(timeout):
    with pytest.raises(ValueError):
        MSFIdentity(settings(request_timeout=timeout))


def test_userinfo_redirect_is_rejected():
    identity = MSFIdentity(settings())
    identity.oauth.session = Mock()
    for method in (identity.oauth.session.get, identity.oauth.session.post):
        response = method.return_value
        response.iter_content.side_effect = lambda chunk_size, r=response: [
            json.dumps(r.json.return_value).encode()
        ]
    identity.oauth.session.get.return_value.status_code = 200
    identity.oauth.session.post.return_value.json.return_value = {"access_token": "access"}
    identity.oauth.session.get.return_value.status_code = 302
    identity.oauth.session.get.return_value.json.return_value = {"sub": "attacker"}
    with pytest.raises(ValueError):
        identity.exchange("code")
