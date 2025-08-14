Pydantic Validation
===================

fastsio can validate event payloads into Pydantic models, similar to FastAPI.

Rules
-----

- Only non-reserved events (not ``connect``/``disconnect``) are parsed into models.
- The client must send a single payload argument (e.g. a dict). If multiple
  arguments are sent, validation will error.
- The handler must annotate a parameter with a Pydantic ``BaseModel`` subclass.

Example
-------

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

Error cases
-----------

- If the payload cannot be validated, an error is raised and the handler is not invoked.
- If more than one payload argument is sent while a model is expected, an error is raised.


