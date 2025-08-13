from pydantic import BaseModel

from src import fastsio
from src.fastsio import SocketID, Environ, Auth, AsyncServer, Data
from src.fastsio.types import Reason

router = fastsio.RouterSIO()


class DataMessage(BaseModel):
    session_id: str
    text: str


@router.on("message.send")
async def message__send(sid: SocketID, sio: AsyncServer, data: DataMessage):
    await sio.emit("message.new", data=data.model_dump(), to=sid)


@router.event
async def connect(sid: SocketID, environ: Environ, auth: Auth):
    print(type(sid), sid)
    print(type(environ), environ)
    print(type(auth), auth)


@router.event
async def disconnect(sid: SocketID, reason: Reason):
    print('disconnect ', sid, reason)


sio = fastsio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
)

sio.add_router(router=router)

app = fastsio.ASGIApp(sio)
