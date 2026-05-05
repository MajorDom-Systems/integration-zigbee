import asyncio
from uuid import UUID

from zigpy.device import Device
from zigpy.zcl import Cluster

from majordom_hub.schemas.automation.events import DeviceParameterChangedEvent
from majordom_hub.schemas.device import CredentialsType, Discovery, NonEmptyStr

"""
ZigBeeDeviceListener is a class for working with ZigBee events.
"""
class ZigBeeDeviceListener:
    def __init__(self, controller, device_id: UUID | None = None, cluster: Cluster | None = None):
        self._controller = controller
        self._device_id = device_id
        self._cluster = cluster

    def attribute_updated(self, attribute_id, value, time):
        cluster = self._cluster
        endpoint_id = cluster.endpoint.endpoint_id
        parameter_id = self._controller._mapper.create_uuid_id(f"attribute_{endpoint_id}/{cluster.cluster_id}/{attribute_id}")
        event = DeviceParameterChangedEvent(device_id=self._device_id, parameter_id=parameter_id, value=value)

        asyncio.create_task(self._controller.dependencies.output.controller_did_receive_device_events(self._controller, [event]))

    def device_joined(self, device: Device):
        """
        Called only after the device is joined to ZigBee network(after start_pairing_window).
        """
        device_id = self._controller._mapper.create_uuid_id(self._controller._mapper.convert_eui64_to_str(device.ieee))
        # Zigbee doesn't have discovery. All devices are connected to the network automatically after opening the network.
        # We keep them in the waiting list until they are paired in majordom, then we move them to the connected list.
        # If they are not paired within 5 minutes, we disconnect them from the network.
        asyncio.create_task(self._controller._disconnect_unpaired_discovery(device_id, device.ieee))

    def device_initialized(self, device: Device):
        """
        Called only after the device is fully initialized in a ZigBee network.
        """
        discovery_id = self._controller._mapper.create_uuid_id(self._controller._mapper.convert_eui64_to_str(device.ieee))
        if discovery_id in self._controller.discoveries.keys():
            self._controller._subscribe(discovery_id, device)
            return
        discovery = Discovery(
            id=discovery_id,
            integration=NonEmptyStr(self._controller.name),
            credentials=CredentialsType.none,
            expiration=None,
            transport=NonEmptyStr("ZIGBEE"),
            device_manufacturer=None,
            device_name=NonEmptyStr(device.name),
            device_category=None,
            device_icon=None,
        )

        self._controller._majordom_discoveries[discovery_id] = discovery
        self._controller._awaiting_zb_discoveries[discovery_id] = device

        asyncio.create_task(self._controller.dependencies.output.controller_did_receive_discovery(self._controller, discovery))

    # TODO: listen for "left", "disconnected", "stopped", etc
