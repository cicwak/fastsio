Middlewares
==========

fastsio provides a flexible middleware system that allows intercepting and modifying
Socket.IO events at various stages of processing. Middlewares can be used for:

- Authentication and authorization
- Logging and monitoring
- Rate limiting
- Data validation and transformation
- Error handling
- Performance monitoring

Basic Concepts
-------------

Middlewares in fastsio follow a chain-of-responsibility pattern where each middleware
can process the request before and after the event handler execution. There are two ways
to implement middlewares:

1. **Method-based**: Override `before_event` and `after_event` methods
2. **Call-based**: Override the `__call__` method for complete control

Middleware Types
----------------

**BaseMiddleware**: Base class for all middlewares with async support
**SyncMiddleware**: Base class for synchronous middlewares
**MiddlewareChain**: Manages execution order of multiple middlewares

Creating Middlewares
-------------------

Method-based Middleware
~~~~~~~~~~~~~~~~~~~~~~~

.. code:: python

    from fastsio import BaseMiddleware

    class LoggingMiddleware(BaseMiddleware):
        def __init__(self):
            super().__init__(events=["message", "join_room"])
        
        async def before_event(self, event: str, sid: str, data: Any, **kwargs):
            """Called before event handler execution."""
            print(f"Processing {event} from {sid}")
            return data
        
        async def after_event(self, event: str, sid: str, response: Any, **kwargs):
            """Called after event handler execution."""
            print(f"Completed {event} from {sid}")
            return response

Call-based Middleware
~~~~~~~~~~~~~~~~~~~~~

.. code:: python

    from fastsio import BaseMiddleware

    class CustomMiddleware(BaseMiddleware):
        async def __call__(self, event: str, sid: str, data: Any, handler, **kwargs):
            """Complete control over middleware execution."""
            # Pre-processing
            modified_data = self.preprocess(data)
            
            # Execute handler
            if asyncio.iscoroutinefunction(handler):
                response = await handler(modified_data, **kwargs)
            else:
                response = handler(modified_data, **kwargs)
            
            # Post-processing
            modified_response = self.postprocess(response)
            
            return modified_response

Synchronous Middleware
~~~~~~~~~~~~~~~~~~~~~~

.. code:: python

    from fastsio import SyncMiddleware

    class SyncLoggingMiddleware(SyncMiddleware):
        def before_event(self, event: str, sid: str, data: Any, **kwargs):
            print(f"Sync: Processing {event} from {sid}")
            return data
        
        def after_event(self, event: str, sid: str, response: Any, **kwargs):
            print(f"Sync: Completed {event} from {sid}")
            return response

Middleware Configuration
-----------------------

Event Filtering
~~~~~~~~~~~~~~~

.. code:: python

    # Apply to specific events
    middleware = LoggingMiddleware(events=["message", "join_room"])
    
    # Apply to single event
    middleware = LoggingMiddleware(events="message")
    
    # Apply to all events (default)
    middleware = LoggingMiddleware()

Namespace Filtering
~~~~~~~~~~~~~~~~~~

.. code:: python

    # Apply to specific namespace
    middleware = AuthMiddleware(namespace="/admin")
    
    # Apply to all namespaces (default)
    middleware = AuthMiddleware()

Global Middlewares
~~~~~~~~~~~~~~~~~

.. code:: python

    # Apply to all events regardless of namespace
    middleware = LoggingMiddleware(global_middleware=True)

Registering Middlewares
----------------------

Adding to Server
~~~~~~~~~~~~~~~

.. code:: python

    from fastsio import AsyncServer

    sio = AsyncServer()
    
    # Add middleware with default settings
    sio.add_middleware(LoggingMiddleware())
    
    # Override middleware settings
    sio.add_middleware(
        AuthMiddleware(),
        events=["join_room", "send_message"],
        namespace="/chat"
    )
    
    # Global middleware
    sio.add_middleware(LoggingMiddleware(), global_middleware=True)

Managing Middlewares
~~~~~~~~~~~~~~~~~~~

.. code:: python

    # Get list of registered middlewares
    middlewares = sio.get_middlewares()
    
    # Remove specific middleware
    sio.remove_middleware(middleware_instance)

Built-in Middlewares
--------------------

Authentication Middleware
~~~~~~~~~~~~~~~~~~~~~~~~

.. code:: python

    from fastsio import auth_middleware

    def check_auth(sid: str, environ: dict) -> bool:
        """Check if user is authenticated."""
        return environ.get("HTTP_AUTHORIZATION") is not None
    
    auth_middleware = auth_middleware(check_auth)
    sio.add_middleware(auth_middleware)

Logging Middleware
~~~~~~~~~~~~~~~~~

.. code:: python

    from fastsio import logging_middleware
    import logging

    logger = logging.getLogger("app")
    log_middleware = logging_middleware(logger)
    sio.add_middleware(log_middleware)

Rate Limiting Middleware
~~~~~~~~~~~~~~~~~~~~~~~~

.. code:: python

    from fastsio import rate_limit_middleware

    # 10 requests per minute
    rate_limit = rate_limit_middleware(max_requests=10, window_seconds=60)
    sio.add_middleware(rate_limit)

Advanced Usage
--------------

Custom Exception Handling
~~~~~~~~~~~~~~~~~~~~~~~~

.. code:: python

    class ErrorHandlingMiddleware(BaseMiddleware):
        async def handle_exception(self, exc: Exception, event: str, sid: str, data: Any, **kwargs):
            """Custom exception handling."""
            if isinstance(exc, PermissionError):
                return {"error": "Access denied", "code": 403}
            elif isinstance(exc, ValueError):
                return {"error": "Invalid data", "code": 400}
            else:
                # Re-raise unexpected exceptions
                raise exc

Data Transformation
~~~~~~~~~~~~~~~~~~

.. code:: python

    class DataTransformMiddleware(BaseMiddleware):
        async def before_event(self, event: str, sid: str, data: Any, **kwargs):
            """Transform incoming data."""
            if isinstance(data, dict):
                data["timestamp"] = time.time()
                data["source"] = sid
            return data
        
        async def after_event(self, event: str, sid: str, response: Any, **kwargs):
            """Transform outgoing response."""
            if isinstance(response, dict):
                response["processed_at"] = time.time()
            return response

Middleware Chaining
~~~~~~~~~~~~~~~~~~

.. code:: python

    # Middlewares execute in the order they are added
    sio.add_middleware(LoggingMiddleware())      # First
    sio.add_middleware(AuthMiddleware())         # Second
    sio.add_middleware(RateLimitMiddleware())    # Third
    
    # Execution flow:
    # 1. LoggingMiddleware.before_event
    # 2. AuthMiddleware.before_event
    # 3. RateLimitMiddleware.before_event
    # 4. Event handler
    # 5. RateLimitMiddleware.after_event
    # 6. AuthMiddleware.after_event
    # 7. LoggingMiddleware.after_event

Performance Considerations
-------------------------

- Middlewares execute for every event, so keep them lightweight
- Use event and namespace filtering to limit middleware execution
- Consider caching expensive operations
- Use `SyncMiddleware` for CPU-bound operations

Best Practices
--------------

1. **Keep middlewares focused**: Each middleware should have a single responsibility
2. **Use appropriate base class**: Use `SyncMiddleware` for sync operations
3. **Filter appropriately**: Use events and namespace filters to limit execution
4. **Handle exceptions gracefully**: Override `handle_exception` for custom error handling
5. **Test thoroughly**: Middlewares can affect all events, so test with various scenarios

Example: Complete Chat Application
---------------------------------

.. code:: python

    from fastsio import AsyncServer, BaseMiddleware, SocketID, Data
    import logging

    # Configure logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # Create server
    sio = AsyncServer(async_mode="asgi")

    # Authentication middleware
    class AuthMiddleware(BaseMiddleware):
        def __init__(self):
            super().__init__(events=["join_room", "send_message"])
        
        async def before_event(self, event: str, sid: str, data: Any, environ: dict = None, **kwargs):
            if not environ or not environ.get("HTTP_AUTHORIZATION"):
                raise PermissionError("Authentication required")
            return data

    # Logging middleware
    class ChatLoggingMiddleware(BaseMiddleware):
        async def before_event(self, event: str, sid: str, data: Any, **kwargs):
            logger.info(f"Chat event: {event} from {sid}")
            return data
        
        async def after_event(self, event: str, sid: str, response: Any, **kwargs):
            logger.info(f"Chat event completed: {event} from {sid}")
            return response

    # Rate limiting middleware
    class ChatRateLimitMiddleware(BaseMiddleware):
        def __init__(self):
            super().__init__()
            self.message_counts = {}
        
        async def before_event(self, event: str, sid: str, data: Any, **kwargs):
            if event == "send_message":
                current_count = self.message_counts.get(sid, 0)
                if current_count >= 10:  # 10 messages per minute
                    raise RuntimeError("Rate limit exceeded")
                self.message_counts[sid] = current_count + 1
            return data

    # Add middlewares
    sio.add_middleware(AuthMiddleware())
    sio.add_middleware(ChatLoggingMiddleware())
    sio.add_middleware(ChatRateLimitMiddleware())

    # Event handlers
    @sio.event
    async def join_room(sid: SocketID, data: Data):
        room = data.get("room", "general")
        await sio.enter_room(sid, room)
        return {"status": "joined", "room": room}

    @sio.event
    async def send_message(sid: SocketID, data: Data):
        room = data.get("room", "general")
        message = data.get("message", "")
        await sio.emit("new_message", {"from": sid, "message": message}, room=room)
        return {"status": "sent"}

This example demonstrates a complete chat application with authentication, logging, and rate limiting middlewares.
