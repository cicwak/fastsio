"""
Dependency Injection system based on ContextVar.

This module provides a FastAPI-style dependency injection system
using Python's ContextVar for managing request-scoped dependencies.
"""

import asyncio
import inspect
from contextvars import ContextVar, copy_context
from typing import Any, Callable, Dict, Optional, Type, TypeVar, Union, get_origin, get_args
from functools import wraps

from .types import SocketID, Environ, Auth, Reason, Data, Event

# Context variables for built-in dependencies
_socket_id: ContextVar[Optional[str]] = ContextVar('socket_id', default=None)
_environ: ContextVar[Optional[dict]] = ContextVar('environ', default=None)
_auth: ContextVar[Optional[dict]] = ContextVar('auth', default=None)
_reason: ContextVar[Optional[str]] = ContextVar('reason', default=None)
_data: ContextVar[Any] = ContextVar('data', default=None)
_event: ContextVar[Optional[str]] = ContextVar('event', default=None)
_server: ContextVar[Any] = ContextVar('server', default=None)

# Registry for custom dependencies
_dependency_registry: Dict[str, Callable] = {}

T = TypeVar('T')


class Depends:
    """
    Dependency marker similar to FastAPI's Depends.
    
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
    """
    Resolve dependencies for a function based on its signature and context variables.
    
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
    dependency_cache = getattr(asyncio.current_task(), '_dependency_cache', None)
    if dependency_cache is None:
        dependency_cache = {}
        setattr(asyncio.current_task(), '_dependency_cache', dependency_cache)
    
    for param_name, param in sig.parameters.items():
        # Skip if already provided explicitly
        if param_name in explicit_kwargs:
            resolved[param_name] = explicit_kwargs[param_name]
            continue
        
        annotation = param.annotation
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
        
        if annotation is Auth:
            auth = _auth.get()
            if auth is not None:
                resolved[param_name] = Auth(auth)
            else:
                # Auth is only available in connect handler
                current_event = _event.get()
                if current_event != "connect":
                    raise ValueError("Auth is only available in connect handler")
            continue
        
        if annotation is Reason:
            reason = _reason.get()
            if reason is not None:
                resolved[param_name] = Reason(reason)
            else:
                # Reason is only available in disconnect handler
                current_event = _event.get()
                if current_event != "disconnect":
                    raise ValueError("Reason is only available in disconnect handler")
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
        
        # Handle Depends() dependencies
        if hasattr(param, 'default') and isinstance(param.default, Depends):
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
        
        # Handle Pydantic models
        try:
            from pydantic import BaseModel as _PydanticBaseModel
            is_pydantic = isinstance(annotation, type) and issubclass(annotation, _PydanticBaseModel)
        except ImportError:
            is_pydantic = False
        
        if is_pydantic:
            data = _data.get()
            if data is None:
                raise ValueError(f"Cannot inject Pydantic model '{annotation.__name__}': no data available")
            
            try:
                # Pydantic v2: model_validate
                if hasattr(annotation, "model_validate"):
                    resolved[param_name] = annotation.model_validate(data)
                else:  # Pydantic v1 fallback
                    resolved[param_name] = annotation.parse_obj(data)
            except Exception as exc:
                raise ValueError(f"Failed to validate payload for '{annotation.__name__}': {exc}") from exc
            continue
    
    return resolved


def inject_dependencies(func: Callable) -> Callable:
    """
    Decorator to automatically inject dependencies into a function.
    
    Usage:
        @inject_dependencies
        async def my_handler(sid: SocketID, data: Data):
            pass
    """
    
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        resolved = await resolve_dependencies(func, **kwargs)
        resolved.update(kwargs)  # Explicit kwargs take precedence
        return await func(**resolved)
    
    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        # For sync functions, we need to run in async context
        async def _resolve():
            return await resolve_dependencies(func, **kwargs)
        
        try:
            loop = asyncio.get_running_loop()
            resolved = loop.run_until_complete(_resolve())
        except RuntimeError:
            # No running loop, create one
            resolved = asyncio.run(_resolve())
        
        resolved.update(kwargs)  # Explicit kwargs take precedence
        return func(**resolved)
    
    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    else:
        return sync_wrapper


async def run_with_context(
    func: Callable,
    socket_id: Optional[str] = None,
    environ: Optional[dict] = None,
    auth: Optional[dict] = None,
    reason: Optional[str] = None,
    data: Any = None,
    event: Optional[str] = None,
    server: Any = None,
    **kwargs
) -> Any:
    """
    Run a function with dependency injection context.
    
    This is the main entry point for executing handlers with DI.
    """
    
    def _run_in_context():
        with DependencyContext(
            socket_id=socket_id,
            environ=environ,
            auth=auth,
            reason=reason,
            data=data,
            event=event,
            server=server,
        ):
            if asyncio.iscoroutinefunction(func):
                return func
            else:
                # For sync functions, resolve dependencies synchronously
                resolved = {}
                # Note: This is a simplified sync resolution - full async resolution
                # would need to be done in the calling context
                return lambda: func(**resolved, **kwargs)
    
    # Create a new context and run the function
    ctx = copy_context()
    
    if asyncio.iscoroutinefunction(func):
        async def _async_run():
            with DependencyContext(
                socket_id=socket_id,
                environ=environ,
                auth=auth,
                reason=reason,
                data=data,
                event=event,
                server=server,
            ):
                resolved = await resolve_dependencies(func, **kwargs)
                resolved.update(kwargs)  # Explicit kwargs take precedence
                return await func(**resolved)
        
        return await _async_run()
    else:
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
                # For sync functions, we can't await resolve_dependencies
                # So we use a simplified resolution
                resolved = _resolve_sync_dependencies(func, **kwargs)
                resolved.update(kwargs)
                return func(**resolved)
        
        return ctx.run(_sync_run)


def _resolve_sync_dependencies(func: Callable, **explicit_kwargs) -> Dict[str, Any]:
    """
    Simplified synchronous dependency resolution for sync handlers.
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
        
        if annotation is Auth:
            auth = _auth.get()
            if auth is not None:
                resolved[param_name] = Auth(auth)
            continue
        
        if annotation is Reason:
            reason = _reason.get()
            if reason is not None:
                resolved[param_name] = Reason(reason)
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
        
        # Handle Depends() dependencies for sync functions
        if hasattr(param, 'default') and isinstance(param.default, Depends):
            dep = param.default
            
            # For sync dependencies, resolve recursively
            if not inspect.iscoroutinefunction(dep.dependency):
                dep_resolved = _resolve_sync_dependencies(dep.dependency)
                result = dep.dependency(**dep_resolved)
                resolved[param_name] = result
            else:
                # Can't resolve async dependencies in sync context
                raise ValueError(f"Cannot use async dependency {dep.dependency.__name__} in sync handler")
            continue
        
        # Handle Pydantic models
        try:
            from pydantic import BaseModel as _PydanticBaseModel
            is_pydantic = isinstance(annotation, type) and issubclass(annotation, _PydanticBaseModel)
        except ImportError:
            is_pydantic = False
        
        if is_pydantic:
            data = _data.get()
            if data is None:
                raise ValueError(f"Cannot inject Pydantic model '{annotation.__name__}': no data available")
            
            try:
                # Pydantic v2: model_validate
                if hasattr(annotation, "model_validate"):
                    resolved[param_name] = annotation.model_validate(data)
                else:  # Pydantic v1 fallback
                    resolved[param_name] = annotation.parse_obj(data)
            except Exception as exc:
                raise ValueError(f"Failed to validate payload for '{annotation.__name__}': {exc}") from exc
            continue
    
    return resolved
