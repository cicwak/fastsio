"""Dependency Injection system based on ContextVar.

This module provides a FastAPI-style dependency injection system
using Python's ContextVar for managing request-scoped dependencies.
"""

import asyncio
import inspect
from contextvars import ContextVar, copy_context
from typing import Any, Callable, Dict, Optional, TypeVar, Union, get_args, get_origin

from .types import Auth, Data, Environ, Event, Reason, SocketID

# Context variables for built-in dependencies
_socket_id: ContextVar[Optional[str]] = ContextVar("socket_id", default=None)
_environ: ContextVar[Optional[dict]] = ContextVar("environ", default=None)
_auth: ContextVar[Optional[dict]] = ContextVar("auth", default=None)
_reason: ContextVar[Optional[str]] = ContextVar("reason", default=None)
_data: ContextVar[Any] = ContextVar("data", default=None)
_event: ContextVar[Optional[str]] = ContextVar("event", default=None)
_server: ContextVar[Any] = ContextVar("server", default=None)

# Registry for custom dependencies
_dependency_registry: Dict[str, Callable] = {}

T = TypeVar("T")


class Depends:
    """Dependency marker similar to FastAPI's Depends.

    Usage:
        def get_database():
            return Database()

        @sio.event
        async def handle_event(sid: SocketID, db: Database = Depends(get_database)):
            pass
    """

    def __init__(self, dependency: Callable[..., T], use_cache: bool = True):
        self.dependency = dependency
        self.use_cache = use_cache
        self._cache_key = f"_dep_cache_{id(dependency)}"


def register_dependency(name: str, factory: Callable) -> None:
    """Register a global dependency factory."""
    _dependency_registry[name] = factory


def get_dependency(name: str) -> Optional[Callable]:
    """Get a registered dependency factory by name."""
    return _dependency_registry.get(name)


class DependencyContext:
    """Context manager for setting up dependency injection context."""

    def __init__(
        self,
        socket_id: Optional[str] = None,
        environ: Optional[dict] = None,
        auth: Optional[dict] = None,
        reason: Optional[str] = None,
        data: Any = None,
        event: Optional[str] = None,
        server: Any = None,
    ):
        self.socket_id = socket_id
        self.environ = environ
        self.auth = auth
        self.reason = reason
        self.data = data
        self.event = event
        self.server = server
        self._tokens = []

    def __enter__(self):
        if self.socket_id is not None:
            self._tokens.append(_socket_id.set(self.socket_id))
        if self.environ is not None:
            self._tokens.append(_environ.set(self.environ))
        if self.auth is not None:
            self._tokens.append(_auth.set(self.auth))
        if self.reason is not None:
            self._tokens.append(_reason.set(self.reason))
        if self.data is not None:
            self._tokens.append(_data.set(self.data))
        if self.event is not None:
            self._tokens.append(_event.set(self.event))
        if self.server is not None:
            self._tokens.append(_server.set(self.server))
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        for token in reversed(self._tokens):
            token.var.reset(token)


async def resolve_dependencies(func: Callable, **explicit_kwargs) -> Dict[str, Any]:
    """Resolve dependencies for a function based on its signature and context variables.

    Args:
        func: The function to resolve dependencies for
        explicit_kwargs: Explicitly provided keyword arguments

    Returns:
        Dictionary of resolved dependencies
    """
    if not callable(func):
        return {}

    sig = inspect.signature(func)
    resolved = {}
    dependency_cache = getattr(asyncio.current_task(), "_dependency_cache", None)
    if dependency_cache is None:
        dependency_cache = {}
        asyncio.current_task()._dependency_cache = dependency_cache

    for param_name, param in sig.parameters.items():
        # Skip if already provided explicitly
        if param_name in explicit_kwargs:
            resolved[param_name] = explicit_kwargs[param_name]
            continue

        annotation = param.annotation

        # Handle Depends() dependencies first (before checking annotation)
        if hasattr(param, "default") and isinstance(param.default, Depends):
            dep = param.default
            cache_key = dep._cache_key

            # Check cache if enabled
            if dep.use_cache and cache_key in dependency_cache:
                resolved[param_name] = dependency_cache[cache_key]
                continue

            # Resolve dependency
            if asyncio.iscoroutinefunction(dep.dependency):
                dep_resolved = await resolve_dependencies(dep.dependency)
                result = await dep.dependency(**dep_resolved)
            else:
                dep_resolved = await resolve_dependencies(dep.dependency)
                result = dep.dependency(**dep_resolved)

            # Cache result if enabled
            if dep.use_cache:
                dependency_cache[cache_key] = result

            resolved[param_name] = result
            continue

        if annotation == inspect.Parameter.empty:
            continue

        # Handle built-in types
        if annotation is SocketID:
            sid = _socket_id.get()
            if sid is not None:
                resolved[param_name] = SocketID(sid)
            continue

        if annotation is Environ:
            environ = _environ.get()
            if environ is not None:
                resolved[param_name] = Environ(environ)
            continue

        # Auth: available only in connect. Inject None if not provided by client.
        if annotation is Auth or (
            get_origin(annotation) is Union and Auth in get_args(annotation)
        ):
            current_event = _event.get()
            if current_event != "connect":
                raise ValueError("Auth is only available in connect handler")
            auth = _auth.get()
            resolved[param_name] = Auth(auth) if auth is not None else None
            continue

        # Reason: available only in disconnect. Inject None if absent.
        if annotation is Reason or (
            get_origin(annotation) is Union and Reason in get_args(annotation)
        ):
            current_event = _event.get()
            if current_event != "disconnect":
                raise ValueError("Reason is only available in disconnect handler")
            reason = _reason.get()
            resolved[param_name] = Reason(reason) if reason is not None else None
            continue

        if annotation is Data:
            data = _data.get()
            if data is not None:
                resolved[param_name] = data
            continue

        if annotation is Event:
            event = _event.get()
            if event is not None:
                resolved[param_name] = Event(event)
            continue

        # Handle AsyncServer injection
        try:
            from .async_server import AsyncServer as _AsyncServerType
        except ImportError:
            _AsyncServerType = None

        if _AsyncServerType and annotation is _AsyncServerType:
            server = _server.get()
            if server is not None:
                resolved[param_name] = server
            continue

        # Handle sync Server injection
        try:
            from .server import Server as _SyncServerType
        except ImportError:
            _SyncServerType = None

        if _SyncServerType and annotation is _SyncServerType:
            server = _server.get()
            if server is not None:
                resolved[param_name] = server
            continue

        # Handle Pydantic models
        try:
            from pydantic import BaseModel as _PydanticBaseModel

            is_pydantic = isinstance(annotation, type) and issubclass(
                annotation, _PydanticBaseModel
            )
        except ImportError:
            is_pydantic = False

        if is_pydantic:
            data = _data.get()
            if data is None:
                raise ValueError(
                    f"Cannot inject Pydantic model '{annotation.__name__}': no data available"
                )

            try:
                # Pydantic v2: model_validate
                if hasattr(annotation, "model_validate"):
                    resolved[param_name] = annotation.model_validate(data)
                else:  # Pydantic v1 fallback
                    resolved[param_name] = annotation.parse_obj(data)
            except Exception as exc:
                raise ValueError(
                    f"Failed to validate payload for '{annotation.__name__}': {exc}"
                ) from exc
            continue

    return resolved


async def run_with_context(
    func: Callable,
    *args: Any,
    socket_id: Optional[str] = None,
    environ: Optional[dict] = None,
    auth: Optional[dict] = None,
    reason: Optional[str] = None,
    data: Any = None,
    event: Optional[str] = None,
    server: Any = None,
    **kwargs: Any,
) -> Any:
    """Run a function with dependency injection context.

    This is the main entry point for executing handlers with DI.
    """
    if asyncio.iscoroutinefunction(func):
        with DependencyContext(
            socket_id=socket_id,
            environ=environ,
            auth=auth,
            reason=reason,
            data=data,
            event=event,
            server=server,
        ):
            # Resolve dependencies and avoid duplicates for positionals
            sig = inspect.signature(func)
            resolved = await resolve_dependencies(func, **kwargs)
            resolved.update(kwargs)  # Explicit kwargs take precedence
            # Remove any resolved entries that are satisfied by positional args
            consumed = 0
            for name, param in sig.parameters.items():
                if param.kind in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                ) and consumed < len(args):
                    if name in resolved:
                        resolved.pop(name)
                    consumed += 1
                elif param.kind == inspect.Parameter.VAR_POSITIONAL:
                    # *args collects remaining positionals; stop consuming by name
                    break
            return await func(*args, **resolved)
    else:
        # Create a new context and run the sync function
        ctx = copy_context()

        def _sync_run():
            with DependencyContext(
                socket_id=socket_id,
                environ=environ,
                auth=auth,
                reason=reason,
                data=data,
                event=event,
                server=server,
            ):
                sig = inspect.signature(func)
                resolved = _resolve_sync_dependencies(func, **kwargs)
                resolved.update(kwargs)
                consumed = 0
                for name, param in sig.parameters.items():
                    if param.kind in (
                        inspect.Parameter.POSITIONAL_ONLY,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    ) and consumed < len(args):
                        if name in resolved:
                            resolved.pop(name)
                        consumed += 1
                    elif param.kind == inspect.Parameter.VAR_POSITIONAL:
                        break
                return func(*args, **resolved)

        return ctx.run(_sync_run)


def _resolve_sync_dependencies(func: Callable, **explicit_kwargs) -> Dict[str, Any]:
    """Simplified synchronous dependency resolution for sync handlers.
    Resolves built-in context variables and sync Depends().
    """
    if not callable(func):
        return {}

    sig = inspect.signature(func)
    resolved = {}

    for param_name, param in sig.parameters.items():
        # Skip if already provided explicitly
        if param_name in explicit_kwargs:
            resolved[param_name] = explicit_kwargs[param_name]
            continue

        annotation = param.annotation

        # Handle Depends() dependencies first (before checking annotation)
        if hasattr(param, "default") and isinstance(param.default, Depends):
            dep = param.default

            # For sync dependencies, resolve recursively
            if not inspect.iscoroutinefunction(dep.dependency):
                dep_resolved = _resolve_sync_dependencies(dep.dependency)
                result = dep.dependency(**dep_resolved)
                resolved[param_name] = result
            else:
                # Can't resolve async dependencies in sync context
                raise ValueError(
                    f"Cannot use async dependency {dep.dependency.__name__} in sync handler"
                )
            continue

        if annotation == inspect.Parameter.empty:
            continue

        # Handle built-in types
        if annotation is SocketID:
            sid = _socket_id.get()
            if sid is not None:
                resolved[param_name] = SocketID(sid)
            continue

        if annotation is Environ:
            environ = _environ.get()
            if environ is not None:
                resolved[param_name] = Environ(environ)
            continue

        if annotation is Auth or (
            get_origin(annotation) is Union and Auth in get_args(annotation)
        ):
            auth = _auth.get()
            resolved[param_name] = Auth(auth) if auth is not None else None
            continue

        if annotation is Reason or (
            get_origin(annotation) is Union and Reason in get_args(annotation)
        ):
            reason = _reason.get()
            resolved[param_name] = Reason(reason) if reason is not None else None
            continue

        if annotation is Data:
            data = _data.get()
            if data is not None:
                resolved[param_name] = data
            continue

        if annotation is Event:
            event = _event.get()
            if event is not None:
                resolved[param_name] = Event(event)
            continue

        # Handle AsyncServer injection
        try:
            from .async_server import AsyncServer as _AsyncServerType
        except ImportError:
            _AsyncServerType = None

        if _AsyncServerType and annotation is _AsyncServerType:
            server = _server.get()
            if server is not None:
                resolved[param_name] = server
            continue

        # Handle sync Server injection
        try:
            from .server import Server as _SyncServerType
        except ImportError:
            _SyncServerType = None

        if _SyncServerType and annotation is _SyncServerType:
            server = _server.get()
            if server is not None:
                resolved[param_name] = server
            continue

        # Handle Pydantic models
        try:
            from pydantic import BaseModel as _PydanticBaseModel

            is_pydantic = isinstance(annotation, type) and issubclass(
                annotation, _PydanticBaseModel
            )
        except ImportError:
            is_pydantic = False

        if is_pydantic:
            data = _data.get()
            if data is None:
                raise ValueError(
                    f"Cannot inject Pydantic model '{annotation.__name__}': no data available"
                )

            try:
                # Pydantic v2: model_validate
                if hasattr(annotation, "model_validate"):
                    resolved[param_name] = annotation.model_validate(data)
                else:  # Pydantic v1 fallback
                    resolved[param_name] = annotation.parse_obj(data)
            except Exception as exc:
                raise ValueError(
                    f"Failed to validate payload for '{annotation.__name__}': {exc}"
                ) from exc
            continue

    return resolved
