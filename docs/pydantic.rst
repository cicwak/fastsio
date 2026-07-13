Typed Payload Validation
========================

fastsio can validate event payloads into Pydantic models and
``msgspec.Struct`` classes, similar to FastAPI.

Rules
-----

- Only non-reserved events (not ``connect``/``disconnect``) are parsed into models.
- The client must send a single payload argument (e.g. a dict). If multiple
  arguments are sent, validation will error.
- The handler must annotate a parameter with a Pydantic ``BaseModel`` subclass
  or a ``msgspec.Struct`` subclass.

Pydantic example
----------------

.. code:: python

    from pydantic import BaseModel
    from fastsio import RouterSIO

    router = RouterSIO()

    class Message(BaseModel):
        text: str
        room: str

    @router.on("message")
    async def on_message(data: Message):
        # data is a validated Pydantic model
        ...

msgpack and msgspec example
---------------------------

.. code:: python

    import msgspec
    from fastsio import AsyncServer, SocketID

    sio = AsyncServer(serializer="msgpack")

    class User(msgspec.Struct):
        name: str
        groups: set[str] = set()
        email: str | None = None

    @sio.event
    async def my_event(sid: SocketID, user: User):
        await sio.emit(
            "my reply",
            {
                "name": user.name,
                "groups": list(user.groups),
                "email": user.email,
            },
            room="chat_users",
            skip_sid=sid,
        )

Error cases
-----------

- If the payload cannot be validated, an error is raised and the handler is not invoked.
- If more than one payload argument is sent while a model is expected, an error is raised.

