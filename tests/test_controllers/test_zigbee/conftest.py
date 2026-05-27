import asyncio
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from httpx_ws import aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport
from jose import jwt
from pytest_asyncio import is_async_test

from majordom_hub.config import VIRTUAL_DISABLED_SERVICES, Settings
from majordom_hub.coordinator import Coordinator
from majordom_hub.providers.paths import Paths
from tests.hardware.iot_cage.aioiotrpc import AioIotRpc

cloud_key = Paths.data.keys.cloud.read_text()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Force all async tests in this directory to share the session event loop."""
    session_scope_marker = pytest.mark.asyncio(loop_scope="session")
    for item in items:
        if is_async_test(item):
            item.add_marker(session_scope_marker, append=False)


@pytest.fixture(autouse=True)
def clear_db():
    pass


@pytest.fixture(scope="session")
def credentials_repo_mock_zb():
    with patch("majordom_hub.repository.credentials_repository.CredentialsRepository._write_file", new_callable=Mock):
        yield


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def cloud_service_mock_zb():
    with (
        patch("majordom_hub.coordinator.CloudService.start", new_callable=AsyncMock),
        patch("majordom_hub.coordinator.CloudService.fetch_all", new_callable=AsyncMock),
        patch("majordom_hub.coordinator.CloudService.send_message", new_callable=AsyncMock) as mock,
    ):
        yield mock


@pytest.fixture(scope="session")
def clear_zigbee_db():
    """Delete the zigbee device DB (+ WAL/SHM) before the real-hardware test session."""
    db = Paths.data.integrations.named("zigbee") / "zigbee.db"
    for suffix in ("", "-wal", "-shm"):
        p = db.parent / (db.name + suffix)
        if p.exists():
            p.unlink()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def coordinator(cloud_service_mock_zb, credentials_repo_mock_zb, clear_zigbee_db):
    with patch("majordom_hub.coordinator.ServerService.start", new_callable=AsyncMock):
        c = Coordinator(settings=Settings(disable_services=VIRTUAL_DISABLED_SERVICES - {"ZigBeeController"}))
        await c.start(wait_forever=False)
        yield c
        await c.stop()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
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


# ---------------------------------------------------------------------------
# Mocked ZigBee fixtures (no real hardware required)
# ---------------------------------------------------------------------------

import zigpy.application
import zigpy.endpoint
import zigpy.profiles
import zigpy.state as app_state
import zigpy.types as t
import zigpy.zdo.types as zdo_t
from zigpy.config import CONF_DATABASE, CONF_DEVICE, CONF_DEVICE_PATH

from majordom_hub.services.controller.framework.relay_controller import RelayController
from majordom_hub.services.service_manager import ServiceProxy

# IEEE 00:11:22:33:44:55:66:77 → discovery_id b10d1e10-189b-5c0f-a68f-6d90c4c07d7f
_MOCK_IEEE = t.EUI64.convert("00:11:22:33:44:55:66:77")
_MOCK_NWK = t.NWK(0x1234)
_NCP_IEEE = t.EUI64.convert("aa:11:22:bb:33:44:be:ef")


def _make_mock_zb_device(app: zigpy.application.ControllerApplication) -> zigpy.device.Device:
    dev = app.add_device(nwk=_MOCK_NWK, ieee=_MOCK_IEEE)
    dev.node_desc = zdo_t.NodeDescriptor(
        logical_type=zdo_t.LogicalType.Router,
        complex_descriptor_available=0,
        user_descriptor_available=0,
        reserved=0,
        aps_flags=0,
        frequency_band=zdo_t.NodeDescriptor.FrequencyBand.Freq2400MHz,
        mac_capability_flags=zdo_t.NodeDescriptor.MACCapabilityFlags.AllocateAddress,
        manufacturer_code=4174,
        maximum_buffer_size=82,
        maximum_incoming_transfer_size=82,
        server_mask=0,
        maximum_outgoing_transfer_size=82,
        descriptor_capability_field=zdo_t.NodeDescriptor.DescriptorCapability.NONE,
    )
    ep = dev.add_endpoint(1)
    ep.status = zigpy.endpoint.Status.ZDO_INIT
    ep.profile_id = 260
    ep.device_type = zigpy.profiles.zha.DeviceType.ON_OFF_LIGHT
    ep.add_input_cluster(6)  # OnOff
    ep.add_input_cluster(8)  # LevelControl
    return dev


class _MockZigpyApp(zigpy.application.ControllerApplication):
    """In-memory zigpy stub — no hardware, no DB."""

    _zb_controller = None  # injected after coordinator.start()

    async def send_packet(self, *_):
        pass

    async def connect(self, *_):
        pass

    async def disconnect(self, *_):
        pass

    async def start_network(self, *_):
        pass

    async def force_remove(self, *_):
        pass

    async def add_endpoint(self, *_):
        pass

    async def permit_ncp(self, *_):
        pass

    async def write_network_info(self, *_):
        pass

    async def reset_network_info(self, *_):
        pass

    async def permit_with_link_key(self, *_):
        pass

    async def shutdown(self, *_):
        pass

    async def load_network_info(self, *, load_devices=False):
        self.state.network_info.channel = 15

    async def permit(self, time_s=60, node=None):
        """Simulate a device joining — triggers device_joined + device_initialized."""
        if self._zb_controller is None:
            return
        dev = _make_mock_zb_device(self)
        self._zb_controller.device_joined(dev)
        await asyncio.sleep(0.05)  # let WS message propagate
        self._zb_controller.device_initialized(dev)

    async def remove(self, ieee):
        if self.get_device(ieee):
            del self.devices[ieee]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def coordinator_mocked(cloud_service_mock_zb, credentials_repo_mock_zb):
    mock_app: list[_MockZigpyApp] = []

    async def _new(config, auto_form=False):
        app = _MockZigpyApp({CONF_DATABASE: None, CONF_DEVICE: {CONF_DEVICE_PATH: "/dev/null"}})
        app.state.node_info = app_state.NodeInfo(nwk=t.NWK(0x0000), ieee=_NCP_IEEE, logical_type=zdo_t.LogicalType.Coordinator)
        mock_app.append(app)
        return app

    with (
        patch("majordom_hub.coordinator.ServerService.start", new_callable=AsyncMock),
        patch("bellows.zigbee.application.ControllerApplication.new", side_effect=_new),
        patch("zigpy.zcl.Cluster.read_attributes", new_callable=AsyncMock, return_value=({}, {})),
        patch("zigpy.zcl.Cluster.write_attributes", new_callable=AsyncMock, return_value=[{}, {}]),
        patch("zigpy.zcl.Cluster.command", new_callable=AsyncMock, return_value=None),
    ):
        c = Coordinator(settings=Settings(disable_services=VIRTUAL_DISABLED_SERVICES - {"ZigBeeController"}))
        await c.start(wait_forever=False)

        # Wire permit() callbacks to ZigBeeController
        for service in c.services:
            real = object.__getattribute__(service, "_real") if isinstance(service, ServiceProxy) else service
            if isinstance(real, RelayController) and "ZigBee" in real._controllers:
                mock_app[0]._zb_controller = real._controllers["ZigBee"]
                break
        else:
            raise RuntimeError("ZigBeeController not found in coordinator services")

        # Clean up stale device from a previous test run
        from majordom_hub.repository.device_repository import DeviceRepository
        from majordom_hub.utils.database import create_async_session as _db

        async with _db() as session:
            repo = DeviceRepository(session)
            if await repo.get(UUID("b10d1e10-189b-5c0f-a68f-6d90c4c07d7f")):
                await repo.delete(UUID("b10d1e10-189b-5c0f-a68f-6d90c4c07d7f"))

        yield c
        await c.stop()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def async_client_mocked(coordinator_mocked):
    async with AsyncClient(transport=ASGITransport(app=coordinator_mocked.server_service.app), base_url="http://testserver") as client:
        yield client


@pytest.fixture(scope="session")
def get_user_bearer_mocked():
    return lambda id: {
        "Authorization": "Bearer "
        + jwt.encode(
            {"role": "access", "user_id": id.hex if isinstance(id, UUID) else id, "is_admin": False, "exp": time.time() + 3600},
            cloud_key,
            algorithm="RS256",
        )
    }


@pytest.fixture(scope="session")
def async_client_ws_connect_mocked(coordinator_mocked, get_user_bearer_mocked):
    @asynccontextmanager
    async def _connect(user_id: UUID):
        async with AsyncClient(transport=ASGIWebSocketTransport(app=coordinator_mocked.server_service.app), base_url="ws://testserver") as client:
            async with aconnect_ws("/v1/ws/user", client, headers=get_user_bearer_mocked(user_id)) as ws:
                yield ws

    return _connect


# ---------------------------------------------------------------------------
# IoT cage fixtures — real hardware control
# ---------------------------------------------------------------------------
# lab-pi5 (192.168.0.109) USB serial port map:
#   /dev/ttyUSB0  →  1a86 CH340         = IoT Cage Arduino
#   /dev/ttyUSB1  →  SiLabs CP2102N     = Zigbee/Thread
#   /dev/ttyUSB2  →  SONOFF ZWave Dongle = Z-Wave controller (not used by hub yet)
_LAB_IOT_CAGE_PORT = "/dev/ttyUSB0"
_LAB_ZIGBEE_DEVICE_IDX = 0  # cage slot wired to the Zigbee DUT


@pytest.fixture(scope="session")
def zigbee_device_idx(request: pytest.FixtureRequest) -> int:
    v = request.config.getoption("--zigbee-device-idx")
    return int(v) if v is not None else _LAB_ZIGBEE_DEVICE_IDX


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def iot_cage(request: pytest.FixtureRequest) -> AsyncGenerator[AioIotRpc, None]:
    port: str = request.config.getoption("--iot-cage-port") or _LAB_IOT_CAGE_PORT
    cage = AioIotRpc(port=port, timeout=8.0)
    await cage.connect()
    try:
        yield cage
    finally:
        try:
            await cage.all_off()
        except Exception:
            pass
        await cage.close()
