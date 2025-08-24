import datetime

from pydantic import BaseModel

from src import fastsio
from src.fastsio import SocketID, Environ, Auth, AsyncServer, Data, AsyncAPIConfig
from src.fastsio.types import Reason, Event

router = fastsio.RouterSIO()


class DataMessage(BaseModel):
    session_id: str
    text: str


class EditMessage(BaseModel):
    session_id: str
    text: str
    time_updated: datetime.datetime


@router.on("message.send", response_model={"message.new": DataMessage})
async def message__send(sid: SocketID, sio: AsyncServer, data: DataMessage):
    await sio.emit("message.new", data=data.model_dump(), to=sid)


@router.on("message.edit")
async def message__edit(sid: SocketID, sio: AsyncServer, data: DataMessage):
    pass


sio = fastsio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    # its default settings for config
    # asyncapi=AsyncAPIConfig(
    #     enabled=True,
    #     url="/asyncapi.json",
    #     expose_yaml=True,
    #     title="Socket.IO API",
    #     version="1.0.0",
    #     description=None,
    #     servers={},
    #     channel_prefix="",
    #     ui_url="/asyncapi",
    # ),
)

sio.add_router(router=router)

app = fastsio.ASGIApp(sio)
