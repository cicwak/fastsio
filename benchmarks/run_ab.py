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
from pathlib import Path

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


def run_variant(variant, connections, duration, port):
    environment = os.environ.copy()
    environment["FASTSIO_BENCH_PORT"] = str(port)
    if variant in {"python", "rust-codec"}:
        environment["FASTSIO_BENCH_NATIVE"] = "1" if variant == "rust-codec" else "0"
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
    elif variant == "native-python":
        environment["FASTSIO_BENCH_ASYNC"] = "1"
        command = [sys.executable, "benchmarks/native_python_server.py"]
    elif variant == "native-core":
        binary = Path("target/release/fastsio-native-benchmark")
        if not binary.exists():
            raise RuntimeError(
                "build native core first: cargo build --release "
                "--features native-benchmark --bin fastsio-native-benchmark"
            )
        command = [str(binary)]
    else:
        raise ValueError(f"unknown benchmark variant: {variant}")
    process = subprocess.Popen(command, env=environment)
    try:
        wait_for_port(process, port)
        url = f"http://127.0.0.1:{port}/socket.io/?EIO=4&transport=websocket"
        result = asyncio.run(run(url, connections, duration))
        result["variant"] = variant
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
    parser.add_argument(
        "--variants",
        nargs="+",
        choices=("python", "rust-codec", "native-python", "native-core"),
        default=("python", "rust-codec", "native-python", "native-core"),
    )
    args = parser.parse_args()
    results = []
    for repeat in range(args.repeats):
        offset = repeat % len(args.variants)
        order = args.variants[offset:] + args.variants[:offset]
        for variant in order:
            result = run_variant(variant, args.connections, args.duration, args.port)
            result["repeat"] = repeat + 1
            results.append(result)

    by_variant = {
        variant: [result for result in results if result["variant"] == variant]
        for variant in args.variants
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
    speedups = {
        variant: round(
            values["events_s"] / medians["python"]["events_s"],
            3,
        )
        for variant, values in medians.items()
        if variant != "python"
    }
    summary = {
        "runs": results,
        "medians": medians,
        "throughput_speedups": speedups,
        "native_python_meets_2x_threshold": speedups.get("native-python", 0) >= 2,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
