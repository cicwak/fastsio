"""Native Rust transport for performance-sensitive Socket.IO services."""

import dis
import inspect
from collections.abc import Callable, Coroutine
from typing import Any

from pydantic import BaseModel

from . import _rust
from .types import Auth, Data, Event, Reason, SocketID


def _can_eagerly_finish(callback: Callable[..., Any]) -> bool:
    """Return true when an async function has no suspension points."""
    if not inspect.iscoroutinefunction(callback):
        return False
    target = inspect.unwrap(callback)
    return not any(
        instruction.opname in {"GET_AWAITABLE", "YIELD_FROM", "YIELD_VALUE"}
        for instruction in dis.get_instructions(target)
    )


def _finish_immediate_coroutine(coroutine: Coroutine[Any, Any, Any]) -> Any:
    """Execute an async function that is statically known not to suspend."""
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    finally:
        coroutine.close()
    raise RuntimeError("eager native handler unexpectedly suspended")


class NativeServer:
    """Socket.IO server whose transport and connection state live in Rust.

    This initial 0.4 API supports event handlers that return acknowledgement
    payloads. Handlers can be synchronous or asynchronous.
    """

    def __init__(self) -> None:
        """Create an unbound native server."""
        self._native = _rust.NativeServer()
        self.handlers: dict[str, Callable[..., Any]] = {}

    def on(
        self,
        event: str,
        handler: Callable[..., Any] | None = None,
        namespace: str = "/",
    ) -> Callable[..., Any]:
        """Register an event handler or return a registration decorator."""

        def register(callback: Callable[..., Any]) -> Callable[..., Any]:
            key = f"{namespace}:{event}"
            self.handlers[key] = callback
            self._native.on(
                event,
                self._compile_handler(callback, event),
                namespace,
            )
            return callback

        return register(handler) if handler is not None else register

    async def serve(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        """Serve until cancelled."""
        await self._native.serve(host, port)

    async def emit(
        self,
        event: str,
        data: Any = None,
        to: str | None = None,
        room: str | None = None,
    ) -> None:
        """Emit to all clients or to a Socket.IO id/room."""
        await self._native.emit(event, data, to or room)

    async def enter_room(self, sid: str, room: str) -> None:
        """Add a socket to a room."""
        self._native.enter_room(sid, room)

    async def leave_room(self, sid: str, room: str) -> None:
        """Remove a socket from a room."""
        self._native.leave_room(sid, room)

    async def rooms(self, sid: str) -> list[str]:
        """Return rooms joined by a socket."""
        return self._native.rooms(sid)

    async def disconnect(self, sid: str) -> None:
        """Disconnect a socket."""
        self._native.disconnect(sid)

    def _compile_handler(
        self,
        callback: Callable[..., Any],
        event: str,
    ) -> Callable[..., Any]:
        """Compile signature-based injection once, outside the message path."""
        signature = inspect.signature(callback)
        parameters = list(signature.parameters.values())
        legacy = len(parameters) == 2 and all(
            parameter.annotation is inspect.Parameter.empty for parameter in parameters
        )
        eager = _can_eagerly_finish(callback)

        def dispatch(socket_id: str, data: Any) -> Any:
            if legacy:
                result = callback(socket_id, data)
            else:
                values: dict[str, Any] = {}
                for parameter in parameters:
                    annotation = parameter.annotation
                    if annotation is SocketID or parameter.name in {"sid", "socket_id"}:
                        values[parameter.name] = SocketID(socket_id)
                    elif isinstance(annotation, type) and issubclass(
                        annotation, BaseModel
                    ):
                        values[parameter.name] = annotation.model_validate(data)
                    elif (
                        annotation is Data
                        or parameter.name == "data"
                        or annotation is Auth
                        or parameter.name == "auth"
                    ):
                        values[parameter.name] = data
                    elif annotation is Reason or parameter.name == "reason":
                        values[parameter.name] = Reason(str(data))
                    elif annotation is Event or parameter.name == "event":
                        values[parameter.name] = Event(event)
                    elif annotation is NativeServer or parameter.name == "server":
                        values[parameter.name] = self
                    elif parameter.default is inspect.Parameter.empty:
                        raise TypeError(
                            "cannot resolve native handler parameter "
                            f"{parameter.name!r}"
                        )
                result = callback(**values)
            if eager:
                return _finish_immediate_coroutine(result)
            return result

        return dispatch
