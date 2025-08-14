from typing import NewType, Union

# Public typing alias for better readability in handler annotations
SocketID = NewType("SocketID", str)
Environ = NewType("Environ", dict)
Auth = NewType("Auth", dict)
Data = Union[dict, str, bytes]
Reason = NewType("Reason", str)
Event = NewType("Event", str)

__all__ = ["SocketID", "Environ", "Auth", "Reason", "Data", "Event"]
