import asyncio
import os
from contextlib import suppress

import pytest

from fastsio import AsyncClient, NativeServer, SocketID
from fastsio.exceptions import ConnectionError as SocketIOConnectionError


pytestmark = pytest.mark.skipif(
    not os.environ.get("FASTSIO_REDIS_URL"),
    reason="FASTSIO_REDIS_URL is required for the distributed adapter test",
)


async def connect_client(client, url):
    deadline = asyncio.get_running_loop().time() + 10
    while True:
        try:
            await client.connect(url, transports=["websocket"])
            return
        except SocketIOConnectionError:
            if asyncio.get_running_loop().time() >= deadline:
                raise
            await asyncio.sleep(0.01)


async def test_native_redis_broadcasts_between_servers(unused_tcp_port_factory):
    redis_url = os.environ["FASTSIO_REDIS_URL"]
    first_port = unused_tcp_port_factory()
    second_port = unused_tcp_port_factory()
    first = NativeServer(redis_url=redis_url)
    second = NativeServer(redis_url=redis_url)

    @first.on("broadcast")
    async def broadcast(sid: SocketID, data, server: NativeServer):
        await server.emit("cluster_message", {"from": str(sid), **data})
        return {"ok": True}

    @second.on("noop")
    def noop(_sid, data):
        return data

    first_task = asyncio.create_task(first.serve(port=first_port))
    second_task = asyncio.create_task(second.serve(port=second_port))
    sender = AsyncClient()
    receiver = AsyncClient()
    received = asyncio.get_running_loop().create_future()

    @receiver.on("cluster_message")
    def cluster_message(data):
        if not received.done():
            received.set_result(data)

    try:
        await asyncio.sleep(0.5)
        await connect_client(sender, f"http://127.0.0.1:{first_port}")
        await connect_client(receiver, f"http://127.0.0.1:{second_port}")
        await asyncio.sleep(0.1)
        assert await sender.call("broadcast", {"text": "across-nodes"}, timeout=2) == {
            "ok": True
        }
        message = await asyncio.wait_for(received, 2)
        assert message["text"] == "across-nodes"
        assert message["from"]
    finally:
        for client in (sender, receiver):
            if client.connected:
                await client.disconnect()
        for task in (first_task, second_task):
            task.cancel()
        for task in (first_task, second_task):
            with suppress(asyncio.CancelledError):
                await task
