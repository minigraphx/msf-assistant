from unittest.mock import Mock

import pytest

from msf_assistant.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        client_id="client-id",
        api_key="api-key",
        redirect_uri="http://localhost/callback",
        request_timeout=12.5,
    )


@pytest.fixture
def session() -> Mock:
    mock = Mock()
    mock.headers = {}
    return mock
