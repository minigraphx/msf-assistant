import json

import pytest

from msf_assistant.auth import TokenSet
from msf_assistant.token_store import KeychainTokenStore, TokenStoreError


class FakeBackend:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[str, str, str | None]] = []

    def get_password(self, service: str, account: str) -> str | None:
        self.calls.append(("get", service, account))
        return self.values.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.calls.append(("set", service, account))
        self.values[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        self.calls.append(("delete", service, account))
        if (service, account) not in self.values:
            raise KeyError("missing")
        del self.values[(service, account)]


def test_roundtrip_stores_versioned_json_with_all_fields() -> None:
    backend = FakeBackend()
    store = KeychainTokenStore("client", "https://oauth.example", backend=backend)
    tokens = TokenSet("access", "DPoP", 3600, "refresh", "openid profile")

    store.save(tokens)

    assert store.load() == tokens
    record = json.loads(next(iter(backend.values.values())))
    assert record == {
        "version": 1,
        "access_token": "access",
        "token_type": "DPoP",
        "expires_in": 3600,
        "refresh_token": "refresh",
        "scope": "openid profile",
    }


def test_load_returns_none_when_no_entry_exists() -> None:
    store = KeychainTokenStore("client", "https://oauth.example", backend=FakeBackend())
    assert store.load() is None


@pytest.mark.parametrize(
    "record",
    [
        "not json",
        json.dumps([]),
        json.dumps({"version": 2, "access_token": "access"}),
        json.dumps(
            {
                "version": True,
                "access_token": "access",
                "token_type": "Bearer",
                "expires_in": None,
                "refresh_token": None,
                "scope": None,
            }
        ),
        json.dumps({"version": 1, "access_token": ""}),
        json.dumps({"version": 1, "access_token": "access", "token_type": 3}),
        json.dumps({"version": 1, "access_token": "access", "expires_in": 0}),
        json.dumps({"version": 1, "access_token": "access", "expires_in": True}),
        json.dumps({"version": 1, "access_token": "access", "refresh_token": 3}),
        json.dumps({"version": 1, "access_token": "access", "scope": []}),
    ],
)
def test_load_rejects_malformed_records_without_echoing_them(record: str) -> None:
    backend = FakeBackend()
    store = KeychainTokenStore("client", "https://oauth.example", backend=backend)
    backend.values[("msf-assistant", store.account)] = record

    with pytest.raises(TokenStoreError) as error:
        store.load()

    assert record not in str(error.value)


@pytest.mark.parametrize("operation", ["get_password", "set_password", "delete_password"])
def test_backend_failures_are_sanitized(operation: str) -> None:
    class FailingBackend(FakeBackend):
        def __getattribute__(self, name: str):
            if name == operation:
                def fail(*args) -> None:
                    raise RuntimeError("secret backend diagnostic")

                return fail
            return super().__getattribute__(name)

    backend = FailingBackend()
    store = KeychainTokenStore("client", "https://oauth.example", backend=backend)
    if operation == "delete_password":
        backend.values[(store.service, store.account)] = "present"
    action = {
        "get_password": store.load,
        "set_password": lambda: store.save(TokenSet("access")),
        "delete_password": store.clear,
    }[operation]

    with pytest.raises(TokenStoreError) as error:
        action()
    assert "secret backend diagnostic" not in str(error.value)


def test_namespace_separates_client_and_oauth_issuer() -> None:
    backend = FakeBackend()
    stores = [
        KeychainTokenStore("client-a", "https://oauth.example", backend=backend),
        KeychainTokenStore("client-b", "https://oauth.example", backend=backend),
        KeychainTokenStore("client-a", "https://other.example", backend=backend),
    ]

    assert len({store.account for store in stores}) == 3
    assert all(store.service == "msf-assistant" for store in stores)


def test_clear_is_idempotent() -> None:
    store = KeychainTokenStore("client", "https://oauth.example", backend=FakeBackend())
    store.clear()
    store.save(TokenSet("access"))
    store.clear()
    store.clear()
    assert store.load() is None


def test_clear_tolerates_entry_disappearing_during_delete() -> None:
    class RacingBackend(FakeBackend):
        def delete_password(self, service: str, account: str) -> None:
            del self.values[(service, account)]
            raise KeyError("entry disappeared")

    backend = RacingBackend()
    store = KeychainTokenStore("client", "https://oauth.example", backend=backend)
    store.save(TokenSet("access"))

    store.clear()

    assert store.load() is None


def test_save_rejects_tokens_that_cannot_be_loaded() -> None:
    backend = FakeBackend()
    store = KeychainTokenStore("client", "https://oauth.example", backend=backend)

    with pytest.raises(TokenStoreError, match="invalid token record"):
        store.save(TokenSet("access", expires_in=0))

    assert backend.values == {}


def test_repr_does_not_expose_identity() -> None:
    store = KeychainTokenStore("sensitive-client", "https://secret.example", backend=FakeBackend())
    assert "sensitive-client" not in repr(store)
    assert "secret.example" not in repr(store)
