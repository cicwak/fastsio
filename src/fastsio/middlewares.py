"""Middleware system for fastsio.

This module provides a flexible middleware system that allows intercepting
and modifying Socket.IO events at various stages of processing.
"""

import asyncio
from abc import ABC
from typing import Any, Callable, Dict, List, Optional, Set, TypeVar, Union

T = TypeVar("T")


class BaseMiddleware(ABC):
    """Base class for all middlewares.

    Middlewares can be implemented in two ways:
    1. Override before_event/after_event methods
    2. Override __call__ method for custom control flow
    """

    def __init__(
        self,
        events: Optional[Union[str, List[str]]] = None,
        namespace: Optional[str] = None,
        global_middleware: bool = True,
    ):
        """Initialize middleware.

        Args:
            events: Specific events to apply middleware to. If None, applies to all events.
                    Can be a single event name or list of event names.
            namespace: Specific namespace to apply middleware to. If None, applies to all namespaces.
            global_middleware: If True, this middleware runs for all events regardless of namespace.
                    If either `events` or `namespace` is specified, this option is ignored and treated as False.
        """
        if events is None:
            self.events: Set[str] = set()
        elif isinstance(events, str):
            self.events = {events}
        else:
            self.events = set(events)

        self.namespace = namespace
        self.global_middleware = False if events or namespace else global_middleware

    def should_run(self, event: str, namespace: Optional[str] = None) -> bool:
        """Check if middleware should run for given event and namespace.

        Args:
            event: Event name
            namespace: Namespace name

        Returns:
            True if middleware should run
        """
        # Check namespace filter
        if self.namespace is not None:
            if namespace != self.namespace:
                return False

        if self.events:
            if event not in self.events:
                return False

        # Global middlewares always run
        if self.global_middleware:
            return True

        return True

    async def before_event(
        self,
        event: str,
        sid: str,
        data: Any,
        namespace: Optional[str] = None,
        environ: Optional[Dict[str, Any]] = None,
        auth: Optional[Dict[str, Any]] = None,
        server: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Called before event handler execution.

        Args:
            event: Event name
            sid: Socket ID
            data: Event data
            namespace: Namespace
            environ: Request environment
            auth: Authentication data (for connect events)
            server: Server instance
            **kwargs: Additional context

        Returns:
            Modified data (or original data if no modification needed)
        """
        return data

    async def after_event(
        self,
        event: str,
        sid: str,
        response: Any,
        namespace: Optional[str] = None,
        environ: Optional[Dict[str, Any]] = None,
        auth: Optional[Dict[str, Any]] = None,
        server: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Called after event handler execution.

        Args:
            event: Event name
            sid: Socket ID
            response: Handler response
            namespace: Namespace
            environ: Request environment
            auth: Authentication data (for connect events)
            server: Server instance
            **kwargs: Additional context

        Returns:
            Modified response (or original response if no modification needed)
        """
        return response

    async def __call__(
        self,
        event: str,
        sid: str,
        data: Any,
        handler: Callable[..., Any],
        namespace: Optional[str] = None,
        environ: Optional[Dict[str, Any]] = None,
        auth: Optional[Dict[str, Any]] = None,
        server: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Custom middleware implementation.

        Override this method for complete control over the middleware flow.
        This method should call the handler and return the result.

        Args:
            event: Event name
            sid: Socket ID
            data: Event data
            handler: Event handler function
            namespace: Namespace
            environ: Request environment
            auth: Authentication data (for connect events)
            server: Server instance
            **kwargs: Additional context

        Returns:
            Handler response
        """
        # Default implementation using before_event/after_event
        try:
            # Pre-processing
            modified_data = await self.before_event(
                event, sid, data, namespace, environ, auth, server, **kwargs
            )

            # Execute handler
            if asyncio.iscoroutinefunction(handler):
                response = await handler(modified_data, **kwargs)
            else:
                response = handler(modified_data, **kwargs)

            # Post-processing
            modified_response = await self.after_event(
                event, sid, response, namespace, environ, auth, server, **kwargs
            )

            return modified_response

        except Exception as e:
            # Handle exceptions in middleware
            return await self.handle_exception(
                e, event, sid, data, namespace, environ, auth, server, **kwargs
            )

    async def handle_exception(
        self,
        exc: Exception,
        event: str,
        sid: str,
        data: Any,
        namespace: Optional[str] = None,
        environ: Optional[Dict[str, Any]] = None,
        auth: Optional[Dict[str, Any]] = None,
        server: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Handle exceptions that occur during middleware execution.

        Override this method to implement custom exception handling.
        By default, re-raises the exception.

        Args:
            exc: The exception that occurred
            event: Event name
            sid: Socket ID
            data: Event data
            namespace: Namespace
            environ: Request environment
            auth: Authentication data
            server: Server instance
            **kwargs: Additional context

        Returns:
            Response to send to client

        Raises:
            The original exception (default behavior)
        """
        raise exc


class SyncMiddleware(BaseMiddleware):
    """Synchronous middleware base class.

    Use this for middlewares that don't need async operations.
    """

    def before_event(
        self,
        event: str,
        sid: str,
        data: Any,
        namespace: Optional[str] = None,
        environ: Optional[Dict[str, Any]] = None,
        auth: Optional[Dict[str, Any]] = None,
        server: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Synchronous version of before_event."""
        return data

    def after_event(
        self,
        event: str,
        sid: str,
        response: Any,
        namespace: Optional[str] = None,
        environ: Optional[Dict[str, Any]] = None,
        auth: Optional[Dict[str, Any]] = None,
        server: Any = None,
        **kwargs,
    ) -> Any:
        """Synchronous version of after_event."""
        return response

    def __call__(
        self,
        event: str,
        sid: str,
        data: Any,
        handler: Callable[..., Any],
        namespace: Optional[str] = None,
        environ: Optional[Dict[str, Any]] = None,
        auth: Optional[Dict[str, Any]] = None,
        server: Any = None,
        **kwargs,
    ) -> Any:
        """Synchronous version of __call__."""
        try:
            # Pre-processing
            modified_data = self.before_event(
                event, sid, data, namespace, environ, auth, server, **kwargs
            )

            # Execute handler
            response = handler(modified_data, **kwargs)

            # Post-processing
            modified_response = self.after_event(
                event, sid, response, namespace, environ, auth, server, **kwargs
            )

            return modified_response

        except Exception as e:
            return self.handle_exception(
                e, event, sid, data, namespace, environ, auth, server, **kwargs
            )

    def handle_exception(
        self,
        exc: Exception,
        event: str,
        sid: str,
        data: Any,
        namespace: Optional[str] = None,
        environ: Optional[Dict[str, Any]] = None,
        auth: Optional[Dict[str, Any]] = None,
        server: Any = None,
        **kwargs,
    ) -> Any:
        """Synchronous version of handle_exception."""
        raise exc


class MiddlewareChain:
    """Chain of middlewares to execute in sequence.
    """

    def __init__(self):
        self.middlewares: List[BaseMiddleware] = []

    def add_middleware(self, middleware: BaseMiddleware) -> None:
        """Add middleware to the chain."""
        self.middlewares.append(middleware)

    def remove_middleware(self, middleware: BaseMiddleware) -> None:
        """Remove middleware from the chain."""
        if middleware in self.middlewares:
            self.middlewares.remove(middleware)

    async def execute(
        self,
        event: str,
        sid: str,
        data: Any,
        handler: Callable[..., Any],
        namespace: Optional[str] = None,
        environ: Optional[Dict[str, Any]] = None,
        auth: Optional[Dict[str, Any]] = None,
        server: Any = None,
        **kwargs,
    ) -> Any:
        """Execute middleware chain.

        Args:
            event: Event name
            sid: Socket ID
            data: Event data
            handler: Event handler function
            namespace: Namespace
            environ: Request environment
            auth: Authentication data
            server: Server instance
            **kwargs: Additional context

        Returns:
            Final response after all middlewares
        """
        if not self.middlewares:
            # No middlewares, just execute handler
            if asyncio.iscoroutinefunction(handler):
                return await handler(data, **kwargs)
            return handler(data, **kwargs)

        # Execute middlewares in order
        current_data = data

        # Pre-processing: before_event for all middlewares
        for middleware in self.middlewares:
            if not middleware.should_run(event, namespace):
                continue

            if isinstance(middleware, SyncMiddleware):
                current_data = middleware.before_event(
                    event, sid, current_data, namespace, environ, auth, server, **kwargs
                )
            else:
                current_data = await middleware.before_event(
                    event, sid, current_data, namespace, environ, auth, server, **kwargs
                )

        # Execute handler
        if asyncio.iscoroutinefunction(handler):
            response = await handler(sid, current_data, **kwargs)
        else:
            response = handler(sid, current_data, **kwargs)

        # Post-processing: after_event for all middlewares (reverse order)
        for middleware in reversed(self.middlewares):
            if not middleware.should_run(event, namespace):
                continue

            if isinstance(middleware, SyncMiddleware):
                response = middleware.after_event(
                    event, sid, response, namespace, environ, auth, server, **kwargs
                )
            else:
                response = await middleware.after_event(
                    event, sid, response, namespace, environ, auth, server, **kwargs
                )

        return response


# Convenience functions for creating common middlewares


def auth_middleware(auth_checker: Callable[[str, Dict[str, Any]], bool]):
    """Create an authentication middleware.

    Args:
        auth_checker: Function that takes sid and environ, returns True if authorized

    Returns:
        Middleware instance
    """

    class AuthMiddleware(BaseMiddleware):
        async def before_event(
            self,
            event: str,
            sid: str,
            data: Any,
            environ: Optional[Dict[str, Any]] = None,
            **kwargs,
        ):
            if not auth_checker(sid, environ or {}):
                raise PermissionError(f"Unauthorized access for {sid}")
            return data

    return AuthMiddleware()


def logging_middleware(logger=None):
    """Create a logging middleware.

    Args:
        logger: Logger instance or None for default

    Returns:
        Middleware instance
    """
    if logger is None:
        import logging

        logger = logging.getLogger("fastsio.middleware")

    class LoggingMiddleware(BaseMiddleware):
        async def before_event(self, event: str, sid: str, data: Any, **kwargs):
            logger.info(f"Event {event} from {sid} with data: {data}")
            return data

        async def after_event(self, event: str, sid: str, response: Any, **kwargs):
            logger.info(f"Event {event} from {sid} returned: {response}")
            return response

    return LoggingMiddleware()


def rate_limit_middleware(max_requests: int, window_seconds: int):
    """Create a rate limiting middleware.

    Args:
        max_requests: Maximum requests per window
        window_seconds: Time window in seconds

    Returns:
        Middleware instance
    """
    import time
    from collections import defaultdict

    class RateLimitMiddleware(BaseMiddleware):
        def __init__(self):
            super().__init__()
            self.requests = defaultdict(list)
            self.max_requests = max_requests
            self.window_seconds = window_seconds

        async def before_event(self, event: str, sid: str, data: Any, **kwargs):
            now = time.time()
            user_requests = self.requests[sid]

            # Remove old requests outside the window
            user_requests[:] = [
                req_time
                for req_time in user_requests
                if now - req_time < self.window_seconds
            ]

            if len(user_requests) >= self.max_requests:
                raise RuntimeError(f"Rate limit exceeded for {sid}")

            user_requests.append(now)
            return data

    return RateLimitMiddleware()
