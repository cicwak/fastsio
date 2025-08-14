from pydantic import BaseModel

from fastsio import SocketID, Environ, Auth, AsyncServer, Data, Reason, ASGIApp, RouterSIO

router = RouterSIO()


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


sio = AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
)

sio.add_router(router=router)

app = ASGIApp(sio)
