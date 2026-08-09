"""Engine.IO v4 / Socket.IO v5 WebSocket load generator."""

import argparse
import asyncio
import json
import statistics
import time

import aiohttp


async def connect(session, url):
    websocket = await session.ws_connect(url, autoping=False, compress=0)
    opened = await websocket.receive_str()
    if not opened.startswith("0"):
        raise RuntimeError(f"unexpected Engine.IO handshake: {opened!r}")
    await websocket.send_str("40")
    connected = await websocket.receive_str()
    if not connected.startswith("40"):
        raise RuntimeError(f"unexpected Socket.IO handshake: {connected!r}")
    return websocket


async def worker(session, url, deadline, worker_id, latencies):
    websocket = await connect(session, url)
    completed = 0
    try:
        while time.perf_counter() < deadline:
            ack_id = completed + 1
            payload = (
                f'42{ack_id}["echo",{{"worker":{worker_id},'
                f'"sequence":{completed},"message":"hello"}}]'
            )
            started = time.perf_counter()
            await websocket.send_str(payload)
            response = await websocket.receive_str()
            if response == "2":
                await websocket.send_str("3")
                response = await websocket.receive_str()
            if not response.startswith(f"43{ack_id}"):
                raise RuntimeError(f"unexpected acknowledgement: {response!r}")
            elapsed = time.perf_counter() - started
            if completed % 20 == 0:
                latencies.append(elapsed * 1000)
            completed += 1
    finally:
        await websocket.close()
    return completed


async def run(url, connections, duration):
    timeout = aiohttp.ClientTimeout(total=duration + 15)
    connector = aiohttp.TCPConnector(limit=0)
    latencies = []
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        # Establish connections before starting the measured interval.
        websockets = await asyncio.gather(
            *(connect(session, url) for _ in range(connections))
        )
        await asyncio.gather(*(websocket.close() for websocket in websockets))
        started = time.perf_counter()
        deadline = started + duration
        counts = await asyncio.gather(
            *(
                worker(session, url, deadline, worker_id, latencies)
                for worker_id in range(connections)
            )
        )
        elapsed = time.perf_counter() - started
    latencies.sort()
    return {
        "connections": connections,
        "duration_s": round(elapsed, 3),
        "completed_events": sum(counts),
        "events_s": round(sum(counts) / elapsed, 1),
        "latency_p50_ms": round(statistics.median(latencies), 3),
        "latency_p95_ms": round(latencies[int(len(latencies) * 0.95)], 3),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765/socket.io/?EIO=4&transport=websocket")
    parser.add_argument("--connections", type=int, default=100)
    parser.add_argument("--duration", type=float, default=5.0)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.url, args.connections, args.duration))))


if __name__ == "__main__":
    main()
