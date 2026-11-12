from uuid import UUID

from majordom_integration_sdk.schemas.event import DeviceParameterChange
from zigpy.zcl import Cluster


class ZBAttributeUpdatedListener:
    def __init__(self, controller, physical_id: UUID, cluster: Cluster):
        self._controller = controller
        self._physical_id = physical_id
        self._cluster = cluster

    def attribute_updated(self, attribute_id, value, time):
        cluster = self._cluster
        endpoint_id = cluster.endpoint.endpoint_id
        parameter_id = self._controller._mapper.attribute_parameter_uuid(
            self._physical_id, endpoint_id, cluster.cluster_id, attribute_id
        )
        event = DeviceParameterChange(
            physical_id=self._physical_id,
            parameter_id=parameter_id,
            value=self._controller._mapper.normalize_zigbee_value(value),
        )

        self._controller._create_task(self._controller.output.controller_did_receive_events(self._controller, [event]))
