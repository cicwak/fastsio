"""
Example demonstrating middleware usage in fastsio.

This example shows how to use different types of middlewares:
- Global middlewares
- Event-specific middlewares
- Namespace-specific middlewares
- Custom middlewares with __call__ method

The middleware system in fastsio provides a flexible way to intercept and modify
Socket.IO events at various stages of processing. Middlewares can be used for:

1. Authentication and authorization
2. Logging and monitoring
3. Rate limiting
4. Data validation and transformation
5. Error handling
6. Performance monitoring

Key Features:
- Two implementation approaches: method-based (before_event/after_event) or call-based (__call__)
- Event and namespace filtering
- Global middlewares that run for all events
- Synchronous and asynchronous support
- Built-in convenience middlewares
- Chain execution with proper order

Usage:
    python middleware_example.py

This will start a server with various middlewares active, demonstrating
the different capabilities of the middleware system.
"""

import asyncio
import logging
from typing import Any, Dict
from fastsio import (
    AsyncServer,
    BaseMiddleware,
    SyncMiddleware,
    auth_middleware,
    logging_middleware,
    rate_limit_middleware,
    SocketID,
    Data,
    Environ,
    Auth,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CustomMiddleware(BaseMiddleware):
    """Custom middleware that modifies data and logs events."""

    def __init__(self):
        super().__init__(events=["message", "join_room"])

    async def before_event(self, event: str, sid: str, data: Any, **kwargs):
        """Modify data before handler execution."""
        logger.info(f"CustomMiddleware: Processing {event} from {sid}")

        if event == "message" and isinstance(data, dict):
            # Add timestamp to message data
            data["timestamp"] = asyncio.get_event_loop().time()
            data["processed_by"] = "CustomMiddleware"

        return data

    async def after_event(self, event: str, sid: str, response: Any, **kwargs):
        """Modify response after handler execution."""
        logger.info(f"CustomMiddleware: Completed {event} from {sid}")

        if isinstance(response, dict):
            response["middleware_processed"] = True

        return response


class AuthCheckMiddleware(BaseMiddleware):
    """Middleware that checks authentication for specific events."""

    def __init__(self):
        super().__init__(events=["join_room", "send_message"])

    async def before_event(
        self, event: str, sid: str, data: Any, environ: dict = None, **kwargs
    ):
        """Check if user is authenticated."""
        # Simulate auth check based on environ
        if environ and environ.get("HTTP_AUTHORIZATION"):
            logger.info(f"AuthCheckMiddleware: {sid} is authenticated")
            return data
        else:
            logger.warning(f"AuthCheckMiddleware: {sid} is not authenticated")
            raise PermissionError(f"Authentication required for {event}")


class SyncLoggingMiddleware(SyncMiddleware):
    """Synchronous middleware for logging."""

    def __init__(self):
        super().__init__(events=["connect", "disconnect"])

    def before_event(self, event: str, sid: str, data: Any, **kwargs):
        """Log before event execution."""
        logger.info(f"SyncLoggingMiddleware: {event} from {sid}")
        return data

    def after_event(self, event: str, sid: str, response: Any, **kwargs):
        """Log after event execution."""
        logger.info(f"SyncLoggingMiddleware: {event} from {sid} completed")
        return response


async def main():
    """Main function demonstrating middleware usage."""

    # Create server
    sio = AsyncServer(async_mode="asgi", cors_allowed_origins="*")

    # Add global logging middleware
    sio.add_middleware(logging_middleware(logger), global_middleware=True)

    # Add rate limiting middleware for all events
    sio.add_middleware(rate_limit_middleware(max_requests=10, window_seconds=60))

    # Add custom middleware for specific events
    sio.add_middleware(CustomMiddleware())

    # Add auth middleware for specific events
    sio.add_middleware(AuthCheckMiddleware())

    # Add sync middleware for connection events
    sio.add_middleware(SyncLoggingMiddleware())

    # Add namespace-specific middleware
    sio.add_middleware(
        logging_middleware(logger), namespace="/admin", events=["admin_action"]
    )

    # Event handlers
    @sio.event
    async def connect(sid: SocketID, environ: Environ, auth: Auth):
        """Handle client connection."""
        logger.info(f"Client {sid} connected")
        return True

    @sio.event
    async def disconnect(sid: SocketID):
        """Handle client disconnection."""
        logger.info(f"Client {sid} disconnected")

    @sio.event
    async def message(sid: SocketID, data: Data):
        """Handle message event."""
        logger.info(f"Message from {sid}: {data}")
        return {"status": "received", "data": data}

    @sio.event
    async def join_room(sid: SocketID, data: Data):
        """Handle join room event."""
        room = data.get("room", "default")
        await sio.enter_room(sid, room)
        logger.info(f"Client {sid} joined room {room}")
        return {"status": "joined", "room": room}

    @sio.event
    async def send_message(sid: SocketID, data: Data):
        """Handle send message event."""
        room = data.get("room", "default")
        message = data.get("message", "")
        await sio.emit("new_message", {"from": sid, "message": message}, room=room)
        return {"status": "sent", "room": room}

    # Namespace-specific handler
    @sio.event
    async def admin_action(sid: SocketID, data: Data):
        """Handle admin action in admin namespace."""
        logger.info(f"Admin action from {sid}: {data}")
        return {"status": "admin_action_processed"}

    # Print registered middlewares
    logger.info("Registered middlewares:")
    for i, middleware in enumerate(sio.get_middlewares()):
        logger.info(f"  {i+1}. {middleware.__class__.__name__}")
        if middleware.events:
            logger.info(f"     Events: {middleware.events}")
        if middleware.namespace:
            logger.info(f"     Namespace: {middleware.namespace}")
        if middleware.global_middleware:
            logger.info("     Global: True")

    logger.info("\nServer ready! Middlewares are active.")
    logger.info("Try connecting and sending events to see middlewares in action.")


if __name__ == "__main__":
    asyncio.run(main())
