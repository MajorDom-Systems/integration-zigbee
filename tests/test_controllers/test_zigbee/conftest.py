import asyncio
import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from httpx_ws import aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport
from jose import jwt

from majordom_hub.coordinator import Coordinator
from majordom_hub.providers.paths import Paths

cloud_key = Paths.data.keys.cloud.read_text()


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def credentials_repo_mock_zb():
    with patch("majordom_hub.repository.credentials_repository.CredentialsRepository._write_file", new_callable=Mock):
        yield


@pytest_asyncio.fixture(scope="session")
async def cloud_service_mock_zb():
    with (
        patch("majordom_hub.coordinator.CloudService.start", new_callable=AsyncMock),
        patch("majordom_hub.coordinator.CloudService.fetch_all", new_callable=AsyncMock),
        patch("majordom_hub.coordinator.CloudService.send_message", new_callable=AsyncMock) as mock,
    ):
        yield mock


@pytest_asyncio.fixture(scope="session")
async def coordinator(cloud_service_mock_zb, credentials_repo_mock_zb):
    with patch("majordom_hub.coordinator.ServerService.start", new_callable=AsyncMock):
        c = Coordinator(is_virtual_mode=True)
        await c.start(wait_forever=False)
        yield c
        await c.stop()


@pytest_asyncio.fixture(scope="session")
async def async_client(coordinator):
    async with AsyncClient(transport=ASGITransport(app=coordinator.server_service.app), base_url="http://testserver") as client:
        yield client


@pytest.fixture(scope="session")
def get_user_bearer():
    return lambda id: {
        "Authorization": "Bearer "
        + jwt.encode(
            {"role": "access", "user_id": id.hex if isinstance(id, UUID) else id, "is_admin": False, "exp": time.time() + 3600},
            cloud_key,
            algorithm="RS256",
        )
    }


@pytest.fixture(scope="session")
def async_client_ws_connect(coordinator, get_user_bearer):
    @asynccontextmanager
    async def _connect(user_id: UUID):
        async with AsyncClient(transport=ASGIWebSocketTransport(app=coordinator.server_service.app), base_url="ws://testserver") as client:
            async with aconnect_ws("/v1/ws/user", client, headers=get_user_bearer(user_id)) as ws:
                yield ws

    return _connect
