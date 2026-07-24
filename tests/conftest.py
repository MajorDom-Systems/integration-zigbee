"""Unit tests for the Zigbee controller.

Drive `ZigBeeController` directly — via the SDK's test dependencies, against an in-memory
zigpy stub (no radio, no DB). The Hub keeps the e2e (mocked-radio) coverage and the
real-hardware tests.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest_asyncio
import zigpy.application
import zigpy.device
import zigpy.endpoint
import zigpy.profiles
import zigpy.state as app_state
import zigpy.types as t
import zigpy.zdo.types as zdo_t
from majordom_integration_sdk.controller import AbstractController
from majordom_integration_sdk.repository import DeviceRepositoryMemory
from majordom_integration_sdk.testing import (
    FakeBLEDiscoveryService,
    FakeSSDPDiscoveryService,
    FakeZeroconfDiscoveryService,
    RecordingControllerOutput,
)

from majordom_zigbee import ZigBeeController

# IEEE 00:11:22:33:44:55:66:77 -> discovery_id 3ad0d5e0-e32d-5b43-ac8f-150aa969c63d
MOCK_IEEE = t.EUI64.convert("00:11:22:33:44:55:66:77")
MOCK_NWK = t.NWK(0x1234)
NCP_IEEE = t.EUI64.convert("aa:11:22:bb:33:44:be:ef")

DEVICE_ID = "3ad0d5e0-e32d-5b43-ac8f-150aa969c63d"
PARAM_COMMAND_ID = "71f0a643-94d1-5def-b4ff-c5a394fafb63"  # toggle command_1/6/2
PARAM_ATTRIBUTE_ID = "d20e6f38-6ad2-5f51-a7b0-0f812b58e5cd"  # on_time attribute_1/6/16385 (Read|Write)


def _make_mock_zb_device(app: zigpy.application.ControllerApplication) -> zigpy.device.Device:
    dev = app.add_device(nwk=MOCK_NWK, ieee=MOCK_IEEE)
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

    _zb_controller = None  # set to the controller after start()

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
        await asyncio.sleep(0.05)
        self._zb_controller.device_initialized(dev)

    async def remove(self, ieee):
        if self.get_device(ieee):
            del self.devices[ieee]


@pytest_asyncio.fixture
async def zigbee():
    """Started ZigBeeController wired to an in-memory zigpy stub, plus the recording output
    and repository so tests can assert on both sides."""
    created: list[_MockZigpyApp] = []

    async def _new(config, auto_form=False):
        app = _MockZigpyApp({"database_path": None, "device": {"path": "/dev/null"}})
        app.state.node_info = app_state.NodeInfo(
            nwk=t.NWK(0x0000), ieee=NCP_IEEE, logical_type=zdo_t.LogicalType.Coordinator
        )
        created.append(app)
        return app

    repository = DeviceRepositoryMemory(integration="ZigBee")
    output = RecordingControllerOutput()

    import tempfile
    from pathlib import Path

    deps = AbstractController.Dependencies(
        output=output,
        make_device_repository=repository.session,
        documents_folder=Path(tempfile.mkdtemp()),
        zeroconf_discovery_service=FakeZeroconfDiscoveryService(),
        ssdp_discovery_service=FakeSSDPDiscoveryService(),
        ble_discovery_service=FakeBLEDiscoveryService(),
        hardware_interfaces=["/dev/null"],
    )

    with (
        patch("bellows.zigbee.application.ControllerApplication.new", side_effect=_new),
        patch("zigpy.zcl.Cluster.read_attributes", new_callable=AsyncMock, return_value=({}, {})),
        patch("zigpy.zcl.Cluster.write_attributes", new_callable=AsyncMock, return_value=[{}, {}]),
        patch("zigpy.zcl.Cluster.command", new_callable=AsyncMock, return_value=None),
    ):
        controller = ZigBeeController(deps)
        controller.radio = "ezsp"  # pin the backend in tests (don't probe the fake /dev/null port)
        await controller.start()
        created[0]._zb_controller = controller
        yield controller, output, repository, created[0]
        await controller.stop()
