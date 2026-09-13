"""Secure persistence for OAuth tokens in the macOS Keychain."""

from __future__ import annotations

import hashlib
import json
import sys
from typing import Protocol

from msf_assistant.auth import TokenSet


class TokenStoreError(RuntimeError):
    """Raised when secure token persistence is unavailable or invalid."""


class KeyringBackend(Protocol):
    """Minimal interface implemented by keyring backends."""

    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


class KeychainTokenStore:
    """Store a token set in the native macOS Keychain."""

    service = "msf-assistant"
    _VERSION = 1
    _FIELDS = {
        "version",
        "access_token",
        "token_type",
        "expires_in",
        "refresh_token",
        "scope",
    }

    def __init__(
        self,
        client_id: str,
        oauth_base_url: str,
        *,
        backend: KeyringBackend | None = None,
    ) -> None:
        identity = f"{client_id}\0{oauth_base_url}".encode()
        self.account = hashlib.sha256(identity).hexdigest()
        self._backend = backend if backend is not None else self._macos_backend()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(account=<redacted>)"

    @staticmethod
    def _macos_backend() -> KeyringBackend:
        if sys.platform != "darwin":
            raise TokenStoreError(
                "Secure token storage requires macOS Keychain; use --no-save for login."
            )
        try:
            from keyring.backends.macOS import Keyring
        except (ImportError, ModuleNotFoundError):
            raise TokenStoreError(
                "Secure token storage is unavailable; install .[local] or use --no-save for login."
            ) from None
        return Keyring()

    def load(self) -> TokenSet | None:
        try:
            serialized = self._backend.get_password(self.service, self.account)
        except Exception:
            raise TokenStoreError("Unable to read tokens from secure storage.") from None
        if serialized is None:
            return None

        try:
            record = json.loads(serialized)
            return self._tokens_from_record(record)
        except (TypeError, ValueError, KeyError):
            raise TokenStoreError("Secure storage contains an invalid token record.") from None

    def save(self, tokens: TokenSet) -> None:
        record = {
            "version": self._VERSION,
            "access_token": tokens.access_token,
            "token_type": tokens.token_type,
            "expires_in": tokens.expires_in,
            "refresh_token": tokens.refresh_token,
            "scope": tokens.scope,
        }
        try:
            self._tokens_from_record(record)
        except (TypeError, ValueError, KeyError):
            raise TokenStoreError("Refusing to store an invalid token record.") from None
        try:
            serialized = json.dumps(record, separators=(",", ":"), allow_nan=False)
            self._backend.set_password(self.service, self.account, serialized)
        except Exception:
            raise TokenStoreError("Unable to write tokens to secure storage.") from None

    def clear(self) -> None:
        try:
            if self._backend.get_password(self.service, self.account) is None:
                return
            self._backend.delete_password(self.service, self.account)
        except Exception:
            try:
                if self._backend.get_password(self.service, self.account) is None:
                    return
            except Exception:
                pass
            raise TokenStoreError("Unable to clear tokens from secure storage.") from None

    @classmethod
    def _tokens_from_record(cls, record: object) -> TokenSet:
        if not isinstance(record, dict) or set(record) != cls._FIELDS:
            raise ValueError("invalid record shape")
        if type(record["version"]) is not int or record["version"] != cls._VERSION:
            raise ValueError("unsupported record version")

        access_token = record["access_token"]
        token_type = record["token_type"]
        expires_in = record["expires_in"]
        refresh_token = record["refresh_token"]
        scope = record["scope"]
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("invalid access token")
        if not isinstance(token_type, str) or not token_type:
            raise ValueError("invalid token type")
        if expires_in is not None and (
            isinstance(expires_in, bool)
            or not isinstance(expires_in, int)
            or expires_in <= 0
        ):
            raise ValueError("invalid expiry")
        if refresh_token is not None and not isinstance(refresh_token, str):
            raise ValueError("invalid refresh token")
        if scope is not None and not isinstance(scope, str):
            raise ValueError("invalid scope")

        return TokenSet(access_token, token_type, expires_in, refresh_token, scope)
