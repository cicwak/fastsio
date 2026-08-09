"""Native Rust transport for performance-sensitive Socket.IO services."""

import dis
import inspect
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from . import _rust
from .dependency import Depends
from .types import Auth, Data, Event, Reason, SocketID


@dataclass(frozen=True)
class _NativeContext:
    socket_id: str
    data: Any
    event: str
    server: "NativeServer"


@dataclass(frozen=True)
class _Binding:
    name: str
    kind: Any
    resolver: Callable[[_NativeContext, dict[str, Any]], Any]


@dataclass(frozen=True)
class _CompiledCallable:
    callback: Callable[..., Any]
    bindings: tuple[_Binding, ...]

    async def invoke(
        self,
        context: _NativeContext,
        cache: dict[str, Any],
    ) -> Any:
        args: list[Any] = []
        kwargs: dict[str, Any] = {}
        for binding in self.bindings:
            value = binding.resolver(context, cache)
            if inspect.isawaitable(value):
                value = await value
            if binding.kind is inspect.Parameter.POSITIONAL_ONLY:
                args.append(value)
            else:
                kwargs[binding.name] = value
        result = self.callback(*args, **kwargs)
        return await result if inspect.isawaitable(result) else result


@dataclass(frozen=True)
class _DependencyResolver:
    plan: _CompiledCallable
    cache_key: str
    use_cache: bool

    def __call__(
        self,
        context: _NativeContext,
        cache: dict[str, Any],
    ) -> Coroutine[Any, Any, Any]:
        return self._resolve(context, cache)

    async def _resolve(
        self,
        context: _NativeContext,
        cache: dict[str, Any],
    ) -> Any:
        if self.use_cache and self.cache_key in cache:
            return cache[self.cache_key]
        result = await self.plan.invoke(context, cache)
        if self.use_cache:
            cache[self.cache_key] = result
        return result


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
        if any(isinstance(parameter.default, Depends) for parameter in parameters):
            plan = self._compile_call_plan(callback, event, {}, ())

            async def dispatch_with_dependencies(socket_id: str, data: Any) -> Any:
                context = _NativeContext(socket_id, data, event, self)
                return await plan.invoke(context, {})

            return dispatch_with_dependencies

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

    def _compile_call_plan(
        self,
        callback: Callable[..., Any],
        event: str,
        memo: dict[int, _CompiledCallable],
        stack: tuple[int, ...],
    ) -> _CompiledCallable:
        """Compile a handler or dependency tree into reusable value resolvers."""
        callback_id = id(callback)
        if callback_id in memo:
            return memo[callback_id]
        if callback_id in stack:
            raise ValueError(
                f"circular native dependency involving {callback.__name__!r}"
            )

        bindings: list[_Binding] = []
        next_stack = (*stack, callback_id)
        for parameter in inspect.signature(callback).parameters.values():
            if parameter.kind in {
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            }:
                continue
            if isinstance(parameter.default, Depends):
                dependency = parameter.default
                dependency_plan = self._compile_call_plan(
                    dependency.dependency,
                    event,
                    memo,
                    next_stack,
                )
                resolver: Callable[[_NativeContext, dict[str, Any]], Any] = (
                    _DependencyResolver(
                        dependency_plan,
                        dependency._cache_key,
                        dependency.use_cache,
                    )
                )
            else:
                resolver = self._compile_value_resolver(parameter, event)
                if resolver is None:
                    continue
            bindings.append(_Binding(parameter.name, parameter.kind, resolver))

        plan = _CompiledCallable(callback, tuple(bindings))
        memo[callback_id] = plan
        return plan

    def _compile_value_resolver(
        self,
        parameter: inspect.Parameter,
        event: str,
    ) -> Callable[[_NativeContext, dict[str, Any]], Any] | None:
        """Compile one built-in injection without per-event introspection."""
        annotation = parameter.annotation
        if annotation is SocketID or parameter.name in {"sid", "socket_id"}:
            return lambda context, _cache: SocketID(context.socket_id)
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return lambda context, _cache: annotation.model_validate(context.data)
        if annotation is Auth or parameter.name == "auth":
            if event != "connect":
                raise ValueError("Auth is only available in connect handler")
            return lambda context, _cache: (
                Auth(context.data) if context.data is not None else None
            )
        if annotation is Reason or parameter.name == "reason":
            if event != "disconnect":
                raise ValueError("Reason is only available in disconnect handler")
            return lambda context, _cache: Reason(str(context.data))
        if annotation is Data or parameter.name == "data":
            return lambda context, _cache: context.data
        if annotation is Event or parameter.name == "event":
            return lambda context, _cache: Event(context.event)
        if annotation is NativeServer or parameter.name == "server":
            return lambda context, _cache: context.server
        if parameter.default is not inspect.Parameter.empty:
            return None
        raise TypeError(f"cannot resolve native handler parameter {parameter.name!r}")
