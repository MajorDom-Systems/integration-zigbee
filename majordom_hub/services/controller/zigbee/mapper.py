import zigpy.types as t

from enum import Flag, Enum
from typing import Callable
from uuid import UUID
from zigpy.types import EUI64
from zigpy.zcl.foundation import ZCLAttributeAccess, DataType, DataTypeId

from majordom_hub.schemas.parameter import ParameterRole, ParameterDataType


class ZigBeeMapper:
    def __init__(
        self,
        device_uuid: Callable[[str], UUID],
        parameter_uuid: Callable[[UUID, str], UUID],
    ):
        # The controller's framework UUID generators (see AbstractController). Every Zigbee
        # id — device from its IEEE address, parameters from their endpoint/cluster/attribute
        # path — is derived through these, so a device's parameters are namespaced under the
        # device and stay identical whether built at pairing, on fetch, or from a live report.
        self._device_uuid = device_uuid
        self._parameter_uuid = parameter_uuid

    # -------------------------------------------------------------------------
    # Identity: Zigbee addresses/paths -> MajorDom UUIDs
    # -------------------------------------------------------------------------

    def device_uuid_from_ieee(self, ieee: str) -> UUID:
        return self._device_uuid(ieee)

    def attribute_parameter_uuid(self, device_id: UUID, endpoint_id: int, cluster_id: int, attribute_id: int) -> UUID:
        return self._parameter_uuid(device_id, f"attribute_{endpoint_id}/{cluster_id}/{attribute_id}")

    def command_parameter_uuid(self, device_id: UUID, endpoint_id: int, cluster_id: int, command_id: int) -> UUID:
        return self._parameter_uuid(device_id, f"command_{endpoint_id}/{cluster_id}/{command_id}")

    def command_field_uuid(self, device_id: UUID, endpoint_id: int, cluster_id: int, command_id: int, field_index: int) -> UUID:
        return self._parameter_uuid(device_id, f"field_{endpoint_id}/{cluster_id}/{command_id}/{field_index}")

    # -------------------------------------------------------------------------
    # Value / type conversions
    # -------------------------------------------------------------------------

    def convert_eui64_to_str(self, data: EUI64) -> str:
        return EUI64.__str__(data)

    def convert_str_to_eui64(self, data: str) -> EUI64:
        return EUI64.convert(data)

    def parse_zigbee_attribute_access(self, access) -> ParameterRole:
        can_read = bool(access & ZCLAttributeAccess.Read)
        can_write = bool(access & ZCLAttributeAccess.Write)

        if can_write:
            return ParameterRole.control

        if can_read:
            return ParameterRole.sensor

        return ParameterRole.event

    def parse_zigbee_data_type(self, zcl_type: int | type) -> ParameterDataType:
        if isinstance(zcl_type, type):
            if issubclass(zcl_type, t.Bool):
                zcl_type = DataTypeId.bool_
            elif issubclass(zcl_type, Flag):
                zcl_type = DataTypeId.map8
            elif issubclass(zcl_type, Enum):
                zcl_type = DataTypeId.enum8
            elif issubclass(zcl_type, t.FixedIntType):
                zcl_type = DataTypeId.uint8
            elif issubclass(zcl_type, float):
                zcl_type = DataTypeId.single
            else:
                zcl_type = DataTypeId.nodata

        type_id = DataTypeId(zcl_type)

        if type_id in {DataTypeId.unk, DataTypeId.nodata}:
            return ParameterDataType.none
        if type_id == DataType.bool_:
            return ParameterDataType.bool
        if type_id in {DataTypeId.enum8, DataTypeId.enum16}:
            return ParameterDataType.enum
        if type_id in {DataTypeId.string, DataTypeId.string16}:
            return ParameterDataType.string
        if type_id in {DataTypeId.semi, DataTypeId.single, DataTypeId.double}:
            return ParameterDataType.decimal
        if type_id in {
            DataTypeId.uint8, DataTypeId.uint16, DataTypeId.uint24, DataTypeId.uint32,
            DataTypeId.uint40, DataTypeId.uint48, DataTypeId.uint56, DataTypeId.uint64,
            DataTypeId.int8, DataTypeId.int16, DataTypeId.int24, DataTypeId.int32,
            DataTypeId.int40, DataTypeId.int48, DataTypeId.int56, DataTypeId.int64,
            DataTypeId.map8, DataTypeId.map16, DataTypeId.map24, DataTypeId.map32,
            DataTypeId.map40, DataTypeId.map48, DataTypeId.map56, DataTypeId.map64,
            DataTypeId.ToD, DataTypeId.date, DataTypeId.UTC,
            DataTypeId.clusterId, DataTypeId.attribId, DataTypeId.bacOID,
        }:
            return ParameterDataType.integer
        if type_id in {
            DataTypeId.data8, DataTypeId.data16, DataTypeId.data24, DataTypeId.data32,
            DataTypeId.data40, DataTypeId.data48, DataTypeId.data56, DataTypeId.data64,
            DataTypeId.octstr, DataTypeId.octstr16,
            DataTypeId.array, DataTypeId.struct, DataTypeId.set, DataTypeId.bag,
            DataTypeId.EUI64, DataTypeId.key128,
        }:
            return ParameterDataType.data

        return ParameterDataType.none
