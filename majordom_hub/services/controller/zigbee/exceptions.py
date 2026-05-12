class ZBConnectionError(Exception):
    """Raised when ZigBee application is not started or not available."""
    pass


class ZBUnexpectedError(Exception):
    """Raised when an unexpected internal error occurs."""
    pass