class ZBConnectionError(Exception):
    """Raised when ZigBee application is not started or not available."""

    pass


class ZBUnexpectedError(Exception):
    """Raised when an unexpected internal error occurs."""

    pass


class ZBOperationError(Exception):
    """Raised when a ZCL operation succeeds at the transport level but the device returns a non-SUCCESS status."""

    pass
