import asyncio
from contextlib import suppress

import aiohttp
from pydantic import BaseModel

from fastsio import (
    AsyncClient,
    Auth,
    BaseMiddleware,
    Depends,
    Event,
    NativeServer,
    Reason,
    SocketID,
)
from fastsio.exceptions import ConnectionError as SocketIOConnectionError


async def receive_frame(websocket):
    while True:
        frame = await websocket.receive_str()
        if frame == "2":
            await websocket.send_str("3")
            continue
        return frame


async def connect(port, namespace="/"):
    session = aiohttp.ClientSession()
    url = f"http://127.0.0.1:{port}/socket.io/?EIO=4&transport=websocket"
    deadline = asyncio.get_running_loop().time() + 3
    while True:
        try:
            websocket = await session.ws_connect(url, autoping=False, compress=0)
            break
        except aiohttp.ClientConnectorError:
            if asyncio.get_running_loop().time() >= deadline:
                await session.close()
                raise
            await asyncio.sleep(0.01)
    assert (await websocket.receive_str()).startswith("0")
    connect_packet = "40" if namespace == "/" else f"40{namespace},"
    await websocket.send_str(connect_packet)
    assert (await receive_frame(websocket)).startswith(connect_packet)
    return session, websocket


async def test_native_server_typed_handler_and_emit(unused_tcp_port):
    class Message(BaseModel):
        text: str

    server = NativeServer()
    connections = []
    disconnections = []

    @server.on("connect")
    def connected(sid: SocketID, auth: Auth):
        connections.append((sid, auth))

    @server.on("disconnect")
    def disconnected(sid: SocketID, reason: Reason):
        disconnections.append((sid, reason))

    @server.on("echo")
    async def echo(sid: SocketID, data: Message):
        return {"sid": str(sid), "text": data.text}

    @server.on("announce")
    async def announce(sid: SocketID, data: Message, server: NativeServer):
        await server.enter_room(sid, "news")
        rooms = await server.rooms(sid)
        await server.emit("pushed", {"text": data.text}, room="news")
        await server.leave_room(sid, "news")
        return {"ok": True, "rooms": rooms}

    task = asyncio.create_task(server.serve(port=unused_tcp_port))
    session, websocket = await connect(unused_tcp_port)
    try:
        for _ in range(100):
            if connections:
                break
            await asyncio.sleep(0.01)
        assert len(connections) == 1
        await websocket.send_str('421["echo",{"text":"hello"}]')
        echo_ack = await receive_frame(websocket)
        assert echo_ack.startswith("431")
        assert '"text":"hello"' in echo_ack

        await websocket.send_str('422["announce",{"text":"news"}]')
        first = await asyncio.wait_for(receive_frame(websocket), 2)
        frames = [first, await asyncio.wait_for(receive_frame(websocket), 2)]
        assert any(frame.startswith('42["pushed"') for frame in frames)
        assert any(
            frame.startswith("432") and '"ok":true' in frame and '"news"' in frame
            for frame in frames
        )
    finally:
        await websocket.close()
        await session.close()
        for _ in range(100):
            if disconnections:
                break
            await asyncio.sleep(0.01)
        assert len(disconnections) == 1
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_native_server_namespace(unused_tcp_port):
    server = NativeServer()

    @server.on("echo", namespace="/chat")
    def echo(_sid, data):
        return data

    task = asyncio.create_task(server.serve(port=unused_tcp_port))
    session, websocket = await connect(unused_tcp_port, namespace="/chat")
    try:
        await websocket.send_str('42/chat,1["echo",{"text":"hello"}]')
        acknowledgement = await receive_frame(websocket)
        assert acknowledgement.startswith("43/chat,1")
        assert '"text":"hello"' in acknowledgement
    finally:
        await websocket.close()
        await session.close()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_native_server_binary_payload_and_ack(unused_tcp_port):
    server = NativeServer()

    @server.on("binary_echo")
    def binary_echo(_sid, data):
        assert data == {
            "blob": b"\x00\x01fastsio",
            "nested": [b"left", {"right": b"right"}],
        }
        return {
            "blob": data["blob"],
            "nested": [data["nested"][0], bytes(data["nested"][1]["right"])],
        }

    @server.on("request_binary_push")
    async def request_binary_push(sid: SocketID, server: NativeServer):
        await server.emit(
            "binary_push",
            {"blob": bytearray(b"server-push")},
            to=sid,
        )
        return {"ok": True}

    task = asyncio.create_task(server.serve(port=unused_tcp_port))
    client = AsyncClient()
    pushed = asyncio.get_running_loop().create_future()

    @client.on("binary_push")
    def binary_push(data):
        pushed.set_result(data)

    url = f"http://127.0.0.1:{unused_tcp_port}"
    deadline = asyncio.get_running_loop().time() + 3
    while True:
        try:
            await client.connect(url, transports=["websocket"])
            break
        except SocketIOConnectionError:
            if asyncio.get_running_loop().time() >= deadline:
                raise
            await asyncio.sleep(0.01)

    try:
        acknowledgement = await client.call(
            "binary_echo",
            {
                "blob": b"\x00\x01fastsio",
                "nested": [b"left", {"right": b"right"}],
            },
            timeout=2,
        )
        assert acknowledgement == {
            "blob": b"\x00\x01fastsio",
            "nested": [b"left", b"right"],
        }
        assert await client.call("request_binary_push", timeout=2) == {"ok": True}
        assert await asyncio.wait_for(pushed, 2) == {"blob": b"server-push"}
    finally:
        if client.connected:
            await client.disconnect()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_native_server_compiled_dependencies(unused_tcp_port):
    class Message(BaseModel):
        text: str

    server = NativeServer()
    calls = {"message": 0, "enriched": 0}

    def get_message(data: Message):
        calls["message"] += 1
        return data.text.upper()

    async def get_enriched(
        sid: SocketID,
        event: str,
        message: str = Depends(get_message),
    ):
        await asyncio.sleep(0)
        calls["enriched"] += 1
        return f"{event}:{sid}:{message}"

    @server.on("dependency_echo")
    async def dependency_echo(
        first: str = Depends(get_enriched),
        second: str = Depends(get_enriched),
        uncached: str = Depends(get_message, use_cache=False),
    ):
        return {"first": first, "same": first == second, "uncached": uncached}

    task = asyncio.create_task(server.serve(port=unused_tcp_port))
    session, websocket = await connect(unused_tcp_port)
    try:
        await websocket.send_str('427["dependency_echo",{"text":"fast"}]')
        acknowledgement = await receive_frame(websocket)
        assert acknowledgement.startswith("437")
        assert '"same":true' in acknowledgement
        assert '"uncached":"FAST"' in acknowledgement
        assert "dependency_echo:" in acknowledgement
        assert calls == {"message": 2, "enriched": 1}

        await websocket.send_str('428["dependency_echo",{"text":"again"}]')
        second_acknowledgement = await receive_frame(websocket)
        assert second_acknowledgement.startswith("438")
        assert '"uncached":"AGAIN"' in second_acknowledgement
        assert calls == {"message": 4, "enriched": 2}
    finally:
        await websocket.close()
        await session.close()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_native_server_middleware_and_exception_handlers(unused_tcp_port):
    class ApplicationError(Exception):
        pass

    class SpecificError(ApplicationError):
        pass

    class MarkerMiddleware(BaseMiddleware):
        async def before_event(self, _event, _sid, data, *_args, **_kwargs):
            return {**data, "before": True}

        async def after_event(self, _event, _sid, response, *_args, **_kwargs):
            return {**response, "after": True}

    server = NativeServer()

    @server.on("pipeline")
    async def pipeline(_sid, data):
        return data

    @server.on("boom")
    def boom(_sid, _data):
        raise SpecificError("spark")

    server.add_middleware(MarkerMiddleware(events="pipeline"))

    @server.exception_handler(ApplicationError)
    def handle_application_error(_exc: ApplicationError):
        return {"handler": "base"}

    @server.exception_handler(SpecificError)
    async def handle_specific_error(
        server: NativeServer,
        sid: SocketID,
        event: Event,
        exc: SpecificError,
    ):
        await asyncio.sleep(0)
        assert server is not None
        return {
            "handler": "specific",
            "sid": str(sid),
            "event": str(event),
            "message": str(exc),
        }

    task = asyncio.create_task(server.serve(port=unused_tcp_port))
    session, websocket = await connect(unused_tcp_port)
    try:
        await websocket.send_str('429["pipeline",{"input":true}]')
        middleware_ack = await receive_frame(websocket)
        assert middleware_ack.startswith("439")
        assert '"before":true' in middleware_ack
        assert '"after":true' in middleware_ack

        await websocket.send_str('4210["boom",{}]')
        exception_ack = await receive_frame(websocket)
        assert exception_ack.startswith("4310")
        assert '"handler":"specific"' in exception_ack
        assert '"event":"boom"' in exception_ack
        assert '"message":"spark"' in exception_ack
    finally:
        await websocket.close()
        await session.close()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
