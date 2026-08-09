"""Socket.IO echo server used by the end-to-end benchmark."""

import os

import fastsio
from fastsio import packet

native = os.environ.get("FASTSIO_BENCH_NATIVE", "0") == "1"
packet.set_native_codec(native)

sio = fastsio.AsyncServer(
    async_mode="asgi",
    async_handlers=False,
    monitor_clients=False,
    transports=["websocket"],
    rust_engineio=native,
)


@sio.on("echo")
async def echo(_sid: fastsio.SocketID, data: fastsio.Data):
    return data


app = fastsio.ASGIApp(sio)
