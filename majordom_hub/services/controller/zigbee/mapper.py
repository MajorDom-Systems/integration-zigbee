from zigpy.types import EUI64


class ZigBeeMapper:
    def convert_str_to_eui64(self, data: str) -> EUI64:
        return EUI64.convert(data)