"""Run baseline and Rust variants against isolated ASGI server processes."""

import argparse
import asyncio
import json
import os
import socket
import statistics
import subprocess
import sys
import time

from websocket_load import run


def wait_for_port(process, port, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("benchmark server exited before accepting connections")
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise TimeoutError("benchmark server did not start")


def run_variant(native, connections, duration, port):
    environment = os.environ.copy()
    environment["FASTSIO_BENCH_NATIVE"] = "1" if native else "0"
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "benchmarks.asgi_echo_server:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "error",
        "--ws",
        "wsproto",
    ]
    process = subprocess.Popen(command, env=environment)
    try:
        wait_for_port(process, port)
        url = f"http://127.0.0.1:{port}/socket.io/?EIO=4&transport=websocket"
        result = asyncio.run(run(url, connections, duration))
        result["variant"] = "rust" if native else "python"
        return result
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--connections", type=int, default=100)
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    results = []
    for repeat in range(args.repeats):
        order = (False, True) if repeat % 2 == 0 else (True, False)
        for native in order:
            result = run_variant(native, args.connections, args.duration, args.port)
            result["repeat"] = repeat + 1
            results.append(result)

    by_variant = {
        variant: [result for result in results if result["variant"] == variant]
        for variant in ("python", "rust")
    }
    medians = {
        variant: {
            "events_s": round(
                statistics.median(result["events_s"] for result in variant_results),
                1,
            ),
            "latency_p50_ms": round(
                statistics.median(
                    result["latency_p50_ms"] for result in variant_results
                ),
                3,
            ),
            "latency_p95_ms": round(
                statistics.median(
                    result["latency_p95_ms"] for result in variant_results
                ),
                3,
            ),
        }
        for variant, variant_results in by_variant.items()
    }
    speedup = medians["rust"]["events_s"] / medians["python"]["events_s"]
    summary = {
        "runs": results,
        "medians": medians,
        "throughput_speedup": round(speedup, 3),
        "meets_2x_threshold": speedup >= 2,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
