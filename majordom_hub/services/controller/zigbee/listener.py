import asyncio
from uuid import UUID

from zigpy.device import Device
from zigpy.zcl import Cluster

from majordom_hub.schemas.automation.events import DeviceParameterChangedEvent
from majordom_hub.schemas.device import CredentialsType, Discovery, NonEmptyStr


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

    def device_initialized(self, device: Device):
        """
        Called only when new devices paired, after start_pairing_window was called.
        """

        discovery_id = self._controller._mapper.create_uuid_id(str(device.ieee))
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

        # Zigbee doesn't have discovery. All devices are connected to the network automatically after opening the network.
        # We keep them in the waiting list until they are paired in majordom, then we move them to the connected list.
        # If they are not paired within 5 minutes, we disconnect them from the network.
        asyncio.create_task(self._controller.dependencies.output.controller_did_receive_discovery(self._controller, discovery))
        asyncio.create_task(self._controller._disconnect_unpaired_discovery(discovery_id, device.ieee))

    # TODO: listen for "joined", handle "joined but not initialized"
    # TODO: listen for "left", "disconnected", "stopped", etc
