import asyncio

from uuid import UUID
from zigpy.zcl import Cluster

from majordom_hub.schemas.automation.events import DeviceParameterChangedEvent


class ZBAttributeUpdatedListener:
    def __init__(self, controller, device_id: UUID, cluster: Cluster):
        self._controller = controller
        self._device_id = device_id
        self._cluster = cluster

    def attribute_updated(self, attribute_id, value, time):
        cluster = self._cluster
        endpoint_id = cluster.endpoint.endpoint_id
        parameter_id = self._controller._mapper.attribute_parameter_uuid(
            self._device_id, endpoint_id, cluster.cluster_id, attribute_id
        )
        event = DeviceParameterChangedEvent(device_id=self._device_id, parameter_id=parameter_id, value=value)

        asyncio.create_task(self._controller.dependencies.output.controller_did_receive_device_events(self._controller, [event]))
