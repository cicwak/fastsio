"""A/B microbenchmarks for the Python and Rust protocol codecs."""

import argparse
import json
import statistics
import time

import fastsio
from engineio import packet as engineio_packet
from fastsio import packet
from fastsio.rust_engineio import Packet as RustEngineIOPacket


def measure(operation, duration, repeats):
    samples = []
    operation()
    for _ in range(repeats):
        count = 0
        started = time.perf_counter()
        deadline = started + duration
        while time.perf_counter() < deadline:
            operation()
            count += 1
        elapsed = time.perf_counter() - started
        samples.append(count / elapsed)
    return {
        "median_ops_s": round(statistics.median(samples)),
        "min_ops_s": round(min(samples)),
        "max_ops_s": round(max(samples)),
    }


def run(native, duration, repeats):
    packet.set_native_codec(native)
    sio_packet = '2["echo",{"message":"hello","sequence":123}]'
    sio_data = ["echo", {"message": "hello", "sequence": 123}]
    eio_class = RustEngineIOPacket if native else engineio_packet.Packet
    eio_packet_text = '4["echo",{"message":"hello","sequence":123}]'

    server = fastsio.Server(async_handlers=False, monitor_clients=False)
    server._handle_eio_connect("benchmark", {})
    server._handle_eio_message("benchmark", "0")

    return {
        "variant": "rust" if native else "python",
        "socketio_decode": measure(
            lambda: packet.Packet(encoded_packet=sio_packet), duration, repeats
        ),
        "socketio_encode": measure(
            lambda: packet.Packet(packet.EVENT, sio_data).encode(), duration, repeats
        ),
        "engineio_decode": measure(
            lambda: eio_class(encoded_packet=eio_packet_text), duration, repeats
        ),
        "server_receive": measure(
            lambda: server._handle_eio_message("benchmark", sio_packet),
            duration,
            repeats,
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=0.75)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    results = [
        run(False, args.duration, args.repeats),
        run(True, args.duration, args.repeats),
    ]
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
