Routers
=======

fastsio provides a lightweight router, ``RouterSIO``, to organize your Socket.IO
handlers in a familiar way.

Basic usage
-----------

.. code:: python

    from fastsio import AsyncServer, RouterSIO

    sio = AsyncServer()
    router = RouterSIO(namespace="/chat")

    @router.on("message")
    async def handle_message(sid: SocketID, data: Data):
        await sio.emit("message", data, room=sid, namespace="/chat")

    # Attach to server
    sio.add_router(router)

Decorators
----------

- ``router.on(event, namespace=None)``: register a handler for an explicit event name.
- ``@router.event(namespace=None)``: shorthand using the function name as event.
- ``@router.exception_handler(ExceptionClass)``: handle exceptions raised by
  handlers registered through this router.

Multiple routers
----------------

You can structure your code into multiple routers and attach them:

.. code:: python

    router_chat = RouterSIO(namespace="/chat")
    router_admin = RouterSIO(namespace="/admin")

    sio.add_routers(router_chat, router_admin)

Nested routers
--------------

Routers can include other routers. The parent router namespace is prefixed to
all handlers registered in the child router:

.. code:: python

    api_router = RouterSIO(namespace="/api")
    chat_router = RouterSIO(namespace="/chat")

    @chat_router.on("message")
    async def handle_message(sid: SocketID, data: Data):
        ...

    api_router.add_router(chat_router)
    sio.add_router(api_router)  # registers "message" on "/api/chat"

Explicit namespaces on child handlers are also prefixed by the parent router:

.. code:: python

    @chat_router.on("connect", namespace="/room")
    async def handle_connect(sid: SocketID, environ: Environ):
        ...

    api_router.add_router(chat_router)
    sio.add_router(api_router)  # registers "connect" on "/api/room"

Router Exception Handlers
-------------------------

Routers can define exception handlers that apply only to handlers registered
through that router:

.. code:: python

    class RoomClosed(Exception):
        pass

    chat_router = RouterSIO(namespace="/chat")

    @chat_router.exception_handler(RoomClosed)
    async def room_closed_handler(
        sio: AsyncServer,
        sid: SocketID,
        exc: RoomClosed,
    ):
        await sio.emit("error", {"message": "room closed"}, to=sid, namespace="/chat")

    @chat_router.on("message")
    async def message(sid: SocketID, data: Data):
        raise RoomClosed()

Router exception handlers take precedence over server-level exception handlers.
Nested routers inherit handlers from parent routers, and child routers can
override them by registering their own handler for the same exception class.

Interplay with class-based namespaces
------------------------------------

Routers register function-based handlers. If you also use class-based namespaces,
attach them to the router via ``router.register_namespace(ns_instance)`` and then
``sio.add_router(router)``.
