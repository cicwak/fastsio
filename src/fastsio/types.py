from typing import NewType, Union

# Public typing alias for better readability in handler annotations
SocketID = NewType("SocketID", str)
Environ = NewType("Environ", dict)
Auth = NewType("Auth", dict)
Data = Union[dict, list, str, bool, None, int, bytes]
Reason = NewType("Reason", str)
Event = NewType("Event", str)
