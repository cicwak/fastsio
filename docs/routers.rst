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

Multiple routers
----------------

You can structure your code into multiple routers and attach them:

.. code:: python

    router_chat = RouterSIO(namespace="/chat")
    router_admin = RouterSIO(namespace="/admin")

    sio.add_routers(router_chat, router_admin)

Interplay with class-based namespaces
------------------------------------

Routers register function-based handlers. If you also use class-based namespaces,
attach them to the router via ``router.register_namespace(ns_instance)`` and then
``sio.add_router(router)``.


