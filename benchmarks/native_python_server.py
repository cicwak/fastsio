"""Native transport benchmark with a real Python acknowledgement handler."""

import asyncio
import os

from fastsio import NativeServer

server = NativeServer()

if os.environ.get("FASTSIO_BENCH_ASYNC") == "1":

    @server.on("echo")
    async def echo(_sid, data):
        return data

else:

    @server.on("echo")
    def echo(_sid, data):
        return data


if __name__ == "__main__":
    port = int(os.environ.get("FASTSIO_BENCH_PORT", "8765"))
    asyncio.run(server.serve(port=port))
