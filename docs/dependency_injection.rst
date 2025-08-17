Dependency Injection
====================

fastsio supports FastAPI-style dependency injection for handler parameters,
using Python's ContextVar for thread-safe, request-scoped dependency management.

What can be injected
--------------------

Built-in Dependencies
~~~~~~~~~~~~~~~~~~~~~

- ``AsyncServer``: the server instance
- ``SocketID``: the current connection id (``sid``)
- ``Environ``: the request environ associated with the connection
- ``Auth``: the ``auth`` payload from the ``connect`` event only
- Pydantic models: validated from a single-argument payload (see below)
- ``Reason``: disconnect reason (only in ``disconnect`` handler)
- ``Data``: raw payload of the event
- ``Event``: name of handled event

Custom Dependencies with Depends()
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You can inject custom dependencies using the ``Depends()`` function, similar to FastAPI:

- Database connections
- Configuration objects
- External services
- Cached computations
- Any callable that returns a value

Basic Examples
--------------

.. code:: python

    from fastsio import AsyncServer, RouterSIO, SocketID, Environ, Auth, Depends
    from pydantic import BaseModel

    router = RouterSIO()

    class Join(BaseModel):
        room: str

    @router.on("connect")
    async def on_connect(sid: SocketID, environ: Environ, auth: Auth, server: AsyncServer):
        # environ is the WSGI/ASGI environ, auth is provided by client or None
        return True

    @router.on("join", namespace="/chat")
    async def on_join(sid: SocketID, server: AsyncServer, data: Join):
        # data is validated as Pydantic model from a single payload argument
        await server.enter_room(sid, data.room, namespace="/chat")

    @router.on("disconnect")
    async def on_disconnect(sid: SocketID, reason: Reason):
        ...

Advanced Examples with Custom Dependencies
-------------------------------------------

Database Dependency
~~~~~~~~~~~~~~~~~~~

.. code:: python

    from fastsio import AsyncServer, RouterSIO, SocketID, Depends
    import asyncpg

    # Global connection pool
    db_pool = None

    async def get_db_connection():
        """Dependency factory for database connections."""
        async with db_pool.acquire() as connection:
            yield connection

    async def get_user_service(db=Depends(get_db_connection)):
        """Service dependency that depends on database."""
        return UserService(db)

    @router.on("get_user")
    async def get_user(
        sid: SocketID,
        server: AsyncServer,
        user_service=Depends(get_user_service),
        data: dict = None
    ):
        user_id = data.get("user_id")
        user = await user_service.get_user(user_id)
        await server.emit("user_data", user.dict(), to=sid)

Configuration Dependency
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code:: python

    from fastsio import AsyncServer, RouterSIO, SocketID, Depends
    from dataclasses import dataclass

    @dataclass
    class AppConfig:
        max_rooms: int = 100
        allow_private_rooms: bool = True

    # Global configuration
    app_config = AppConfig(max_rooms=50, allow_private_rooms=False)

    def get_config():
        """Get application configuration."""
        return app_config

    @router.on("create_room")
    async def create_room(
        sid: SocketID,
        server: AsyncServer,
        config: AppConfig = Depends(get_config),
        data: dict = None
    ):
        if not config.allow_private_rooms and data.get("private"):
            await server.emit("error", {"message": "Private rooms not allowed"}, to=sid)
            return
        
        # Create room logic...

Caching Dependencies
~~~~~~~~~~~~~~~~~~~~

.. code:: python

    from fastsio import AsyncServer, RouterSIO, SocketID, Depends
    import redis.asyncio as redis

    # Global Redis connection
    redis_client = None

    async def get_redis():
        """Get Redis connection."""
        return redis_client

    async def get_cached_data(
        redis_conn=Depends(get_redis),
        cache_key: str = "default"
    ):
        """Cached dependency with automatic caching."""
        cached = await redis_conn.get(f"cache:{cache_key}")
        if cached:
            return json.loads(cached)
        
        # Expensive computation
        data = await expensive_computation()
        await redis_conn.setex(f"cache:{cache_key}", 300, json.dumps(data))
        return data

    @router.on("get_stats")
    async def get_stats(
        sid: SocketID,
        server: AsyncServer,
        stats=Depends(lambda: get_cached_data(cache_key="stats"))
    ):
        await server.emit("stats", stats, to=sid)

Global Dependency Registration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You can register dependencies globally for reuse across your application:

.. code:: python

    from fastsio import register_dependency
    import asyncpg

    # Register global dependencies
    async def create_db_pool():
        return await asyncpg.create_pool("postgresql://...")

    async def get_db():
        pool = await create_db_pool()
        async with pool.acquire() as conn:
            yield conn

    # Register the dependency
    register_dependency("database", get_db)

    # Use in handlers
    @router.on("query_data")
    async def query_data(
        sid: SocketID,
        server: AsyncServer,
        db=Depends("database")  # Reference by name
    ):
        result = await db.fetch("SELECT * FROM users")
        await server.emit("data", [dict(r) for r in result], to=sid)

How It Works
------------

ContextVar-based System
~~~~~~~~~~~~~~~~~~~~~~~

The new dependency injection system uses Python's ``ContextVar`` to manage 
request-scoped dependencies. This provides:

- **Thread-safe**: Each request runs in its own context
- **Async-safe**: Works correctly with asyncio and concurrent requests
- **Scoped**: Dependencies are automatically cleaned up after request completion
- **Efficient**: Minimal overhead compared to traditional DI systems

Dependency Resolution
~~~~~~~~~~~~~~~~~~~~~

1. When a handler is called, fastsio creates a new context
2. Built-in dependencies (SocketID, Data, etc.) are set in ContextVar
3. Custom dependencies are resolved by calling their factory functions
4. Dependencies can depend on other dependencies (dependency graph)
5. Results are cached within the request scope to avoid recomputation
6. Context is automatically cleaned up after the handler completes

Notes
-----

- ``Auth`` is only available in the ``connect`` handler. Using it elsewhere raises an error.
- ``Reason`` is only available in the ``disconnect`` handler.
- Pydantic validation requires a single payload argument for the event.
- Dependencies are resolved lazily - only when actually needed
- Circular dependencies are not supported and will raise an error
- This is intentionally similar to FastAPI: annotate parameters to receive validated/injected values.

Synchronous Server Support
---------------------------

The new dependency injection system works with both AsyncServer and synchronous Server:

**AsyncServer (async handlers):**

.. code:: python

    from fastsio import AsyncServer, SocketID, Depends

    sio = AsyncServer()

    async def get_service():
        return await create_async_service()

    @sio.on("handler")
    async def handler(
        sid: SocketID,
        service=Depends(get_service)
    ):
        result = await service.process()
        await sio.emit("result", result, to=sid)

**Synchronous Server (sync handlers):**

.. code:: python

    from fastsio import Server, SocketID, Depends

    sio = Server()

    def get_service():  # Sync dependency
        return create_sync_service()

    @sio.on("handler")
    def handler(  # Sync handler
        sid: SocketID,
        server: Server,
        service=Depends(get_service)
    ):
        result = service.process()
        server.emit("result", result, to=sid)

**Important Notes for Sync Server:**

- Sync handlers can only use sync dependencies (non-async functions)
- All dependency injection features work: Pydantic validation, custom dependencies, etc.
- AsyncAPI documentation is fully supported
- Thread-safe ContextVar ensures proper isolation between requests

Migration from Old System
-------------------------

The old dependency injection system is still supported for backward compatibility,
but it's recommended to migrate to the new ContextVar-based system:

**Old way:**

.. code:: python

    @router.on("handler")
    async def handler(sid: SocketID, server: AsyncServer, data: Data):
        # Dependencies injected via parameter inspection
        pass

**New way (same syntax, better implementation):**

.. code:: python

    @router.on("handler")  
    async def handler(sid: SocketID, server: AsyncServer, data: Data):
        # Dependencies injected via ContextVar
        pass

**With custom dependencies:**

.. code:: python

    def get_service():
        return MyService()

    @router.on("handler")
    async def handler(
        sid: SocketID, 
        server: AsyncServer, 
        data: Data,
        service: MyService = Depends(get_service)
    ):
        # Custom dependency injected
        pass


