import copy
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, Union

from . import base_namespace
from .dependency import is_payload_model


class RouterSIO:
    """A lightweight router for organizing Socket.IO event handlers.

    This provides a FastAPI-like developer experience for grouping and
    including handlers. Handlers registered on the router can later be
    attached to a server via ``sio.add_router(router)``.

    Example:
        router = RouterSIO(namespace="/chat")

        @router.on("message")
        async def handle_message(sid: SocketID, data: Data):
            ...

        sio.add_router(router)
    """

    def __init__(self, namespace: Optional[str] = None) -> None:
        # Default namespace applied when not provided explicitly in .on()/@event
        self.default_namespace: str = namespace or "/"
        # Decorator-based function handlers: {namespace: {event: handler}}
        self.handlers: Dict[str, Dict[str, Callable[..., Any]]] = {}
        self.exception_handlers: Dict[Type[BaseException], Callable[..., Any]] = {}
        # Class-based namespace handlers to be registered on the server
        self._namespace_handlers: List[base_namespace.BaseServerNamespace] = []
        # Child routers that inherit this router's default namespace.
        self._routers: List["RouterSIO"] = []

    # Public API mirrors Server.on
    def on(
        self,
        event: str,
        handler: Optional[Callable[..., Any]] = None,
        namespace: Optional[str] = None,
        *,
        response_model: Optional[Union[Any, Dict[str, Any]]] = None,
        channel: Optional[str] = None,
        # asyncapi_from_ast: bool = False,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        ns = namespace or self.default_namespace

        def set_handler(h: Callable[..., Any]) -> Callable[..., Any]:
            if ns not in self.handlers:
                self.handlers[ns] = {}
            # Set AsyncAPI-related metadata and response validation on function
            eff_resp_model = response_model
            if eff_resp_model is not None:
                try:
                    # Validate response_model structure
                    if isinstance(eff_resp_model, dict):
                        # Validate that all values are supported payload models or valid types
                        for event_name, model in eff_resp_model.items():
                            if not isinstance(event_name, str):
                                raise ValueError(
                                    f"response_model keys must be strings, got {type(event_name)}"
                                )
                            # Check if it's a supported payload model (basic check)
                            if hasattr(model, "__bases__"):
                                if not is_payload_model(model):
                                    raise ValueError(
                                        f"response_model['{event_name}'] must be a supported payload model, got {type(model)}"
                                    )

                    h._fastsio_response_model = eff_resp_model
                except Exception:
                    pass
            if channel is not None:
                try:
                    h._fastsio_channel_override = channel
                except Exception:
                    pass
            self.handlers[ns][event] = h
            return h

        if handler is None:
            return set_handler
        set_handler(handler)
        return set_handler

    # Convenience decorator mirrors Server.event
    def event(
        self, *args: Any, **kwargs: Any
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        if len(args) == 1 and len(kwargs) == 0 and callable(args[0]):
            # invoked without arguments: @router.event
            return self.on(args[0].__name__)(args[0])

        # invoked with arguments: @router.event(namespace="...")
        def set_handler(h: Callable[..., Any]) -> Callable[..., Any]:
            return self.on(h.__name__, *args, **kwargs)(h)

        return set_handler

    def exception_handler(
        self, exception_class: Type[BaseException]
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a router-level exception handler."""
        if not isinstance(exception_class, type) or not issubclass(
            exception_class, BaseException
        ):
            raise ValueError(
                "Exception handler must be registered for an exception class"
            )

        def set_handler(handler: Callable[..., Any]) -> Callable[..., Any]:
            self.exception_handlers[exception_class] = handler
            return handler

        return set_handler

    def register_namespace(
        self, namespace_handler: base_namespace.BaseServerNamespace
    ) -> None:
        """Queue a class-based namespace handler for registration.

        The actual registration occurs when the router is attached to a server
        via ``sio.add_router(router)``.
        """
        if not isinstance(namespace_handler, base_namespace.BaseServerNamespace):  # type: ignore[redundant-expr]
            raise ValueError("Not a namespace instance")
        self._namespace_handlers.append(namespace_handler)

    def add_router(self, router: "RouterSIO") -> None:
        """Include another router under this router's default namespace."""
        if not isinstance(router, RouterSIO):
            raise ValueError("Not a router instance")
        self._routers.append(router)

    def add_routers(self, *routers: "RouterSIO") -> None:
        """Include multiple routers under this router's default namespace."""
        for router in routers:
            self.add_router(router)

    # Internal helpers used by the server when attaching the router
    def iter_function_handlers(
        self,
    ) -> List[
        Tuple[
            str,
            str,
            Callable[..., Any],
            Dict[Type[BaseException], Callable[..., Any]],
        ]
    ]:
        return self._iter_function_handlers("/", {})

    def _iter_function_handlers(
        self,
        namespace_prefix: str,
        parent_exception_handlers: Dict[Type[BaseException], Callable[..., Any]],
    ) -> List[
        Tuple[
            str,
            str,
            Callable[..., Any],
            Dict[Type[BaseException], Callable[..., Any]],
        ]
    ]:
        out: List[
            Tuple[
                str,
                str,
                Callable[..., Any],
                Dict[Type[BaseException], Callable[..., Any]],
            ]
        ] = []
        exception_handlers = {
            **parent_exception_handlers,
            **self.exception_handlers,
        }
        for ns, events in self.handlers.items():
            for event, handler in events.items():
                out.append(
                    (
                        self._compose_namespace(namespace_prefix, ns),
                        event,
                        handler,
                        exception_handlers.copy(),
                    )
                )
        child_prefix = self._compose_namespace(namespace_prefix, self.default_namespace)
        for router in self._routers:
            out.extend(router._iter_function_handlers(child_prefix, exception_handlers))
        return out

    def iter_namespace_handlers(self) -> List[base_namespace.BaseServerNamespace]:
        return self._iter_namespace_handlers("/")

    def _iter_namespace_handlers(
        self, namespace_prefix: str
    ) -> List[base_namespace.BaseServerNamespace]:
        out: List[base_namespace.BaseServerNamespace] = []
        for namespace_handler in self._namespace_handlers:
            namespace = str(namespace_handler.namespace or "/")
            composed_namespace = self._compose_namespace(
                namespace_prefix, namespace
            )
            if composed_namespace == namespace:
                out.append(namespace_handler)
            else:
                prefixed_handler = copy.copy(namespace_handler)
                prefixed_handler.namespace = composed_namespace
                out.append(prefixed_handler)
        child_prefix = self._compose_namespace(namespace_prefix, self.default_namespace)
        for router in self._routers:
            out.extend(router._iter_namespace_handlers(child_prefix))
        return out

    @staticmethod
    def _compose_namespace(prefix: str, namespace: str) -> str:
        if prefix == "*" or namespace == "*":
            return "*"
        prefix = prefix or "/"
        namespace = namespace or "/"
        if prefix == "/":
            combined = namespace
        elif namespace == "/":
            combined = prefix
        else:
            combined = f"{prefix.rstrip('/')}/{namespace.lstrip('/')}"
        return "/" + "/".join(part for part in combined.split("/") if part)
