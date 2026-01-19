from uuid import UUID, NAMESPACE_DNS, uuid5
from zigpy.types import EUI64
from zigpy.zcl.foundation import ZCLAttributeAccess, DataType

from majordom_hub.schemas.parameter import ParameterRole, ParameterDataType


class ZigBeeMapper:
    def convert_str_to_eui64(self, data: str) -> EUI64:
        return EUI64.convert(data)

    def parse_zigbee_attribute_access(self, access) -> ParameterRole:
        if bool(access & ZCLAttributeAccess.Read):
            return ParameterRole.sensor
        elif bool(access & ZCLAttributeAccess.Write):
            return ParameterRole.control
        else:
            return ParameterRole.event

    def parse_zigbee_data_type(self, data_type: DataType) -> ParameterDataType:
        if data_type in (DataType.unk, DataType.nodata):
            return ParameterDataType.none

        if data_type == DataType.bool_:
            return ParameterDataType.bool

        if data_type in (DataType.enum8, DataType.enum16):
            return ParameterDataType.enum

        if data_type in (
            DataType.string,
            DataType.string16,
        ):
            return ParameterDataType.string

        if data_type in (
            DataType.semi,
            DataType.single,
            DataType.double,
        ):
            return ParameterDataType.decimal

        if data_type in {
            DataType.uint8, DataType.uint16, DataType.uint24, DataType.uint32,
            DataType.uint40, DataType.uint48, DataType.uint56, DataType.uint64,
            DataType.int8, DataType.int16, DataType.int24, DataType.int32,
            DataType.int40, DataType.int48, DataType.int56, DataType.int64,
            DataType.map8, DataType.map16, DataType.map24, DataType.map32,
            DataType.map40, DataType.map48, DataType.map56, DataType.map64,
            DataType.ToD, DataType.date, DataType.UTC,
            DataType.clusterId, DataType.attribId, DataType.bacOID,
        }:
            return ParameterDataType.integer

        if data_type in {
            DataType.data8, DataType.data16, DataType.data24, DataType.data32,
            DataType.data40, DataType.data48, DataType.data56, DataType.data64,
            DataType.octstr, DataType.octstr16,
            DataType.array, DataType.struct, DataType.set, DataType.bag,
            DataType.EUI64, DataType.key128,
        }:
            return ParameterDataType.data

        return ParameterDataType.none

    def parse_zigbee_data_type_value_to_bytes(self, value, data_type: ParameterDataType) -> bytes:
        if data_type == ParameterDataType.none:
            return bytes()
        if data_type == ParameterDataType.bool:
            return bytes((int(value),))
        if data_type in (ParameterDataType.integer, ParameterDataType.decimal):
            return value.to_bytes()
        if data_type == ParameterDataType.string:
            return bytes(value, 'utf-8')
        if data_type == ParameterDataType.enum:
            return bytes()
        if data_type == ParameterDataType.data:
            return bytes()

    def create_uuid_id(self, id: str) -> UUID:
        return uuid5(NAMESPACE_DNS, id)

