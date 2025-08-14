Dependency Injection
====================

fastsio supports FastAPI-style dependency injection for handler parameters,
driven by type annotations.

What can be injected
--------------------

- ``AsyncServer``: the server instance
- ``SocketID``: the current connection id (``sid``)
- ``Environ``: the request environ associated with the connection
- ``Auth``: the ``auth`` payload from the ``connect`` event only
- Pydantic models: validated from a single-argument payload (see below)
- ``Reason``: disconnect reason (only in ``disconnect`` handler)
- ``Data``: raw payload of the event

Examples
--------

.. code:: python

    from fastsio import AsyncServer, RouterSIO, SocketID, Environ, Auth
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

Notes
-----

- ``Auth`` is only available in the ``connect`` handler. Using it elsewhere raises an error.
- Pydantic validation requires a single payload argument for the event.
- This is intentionally similar to FastAPI: annotate parameters to receive validated/injected values.


