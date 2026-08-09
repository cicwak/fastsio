# Rust Engine.IO experiment

This benchmark is the go/no-go test for rewriting more of fastsio's Engine.IO
stack in Rust. The decision threshold is at least 2x the end-to-end throughput
of the Python baseline under the same workload.

## Scope

The prototype moves these protocol hot paths to a PyO3 extension:

- Engine.IO packet framing, JSON parsing, and base64 handling;
- Socket.IO packet header, namespace, acknowledgement id, and JSON parsing;
- Socket.IO packet encoding for non-binary packets.

The ASGI transport, connection state, queues, dispatch, dependency injection,
and application handler remain in Python. `rust_engineio=True` currently swaps
python-engineio's packet class process-wide because python-engineio does not
provide a per-server codec hook.

## Reproduce

```bash
poetry install
poetry run maturin develop --release
poetry run python benchmarks/codec_benchmark.py --duration 0.75 --repeats 7
poetry run python benchmarks/run_ab.py --connections 100 --duration 5 --repeats 3
```

The A/B runner alternates variant order and starts a fresh uvicorn process for
every sample. Each client keeps one acknowledgement in flight, so 100 clients
create 100 concurrent request/acknowledgement loops over real WebSockets.

## Reference result

Measured on 2026-08-10 using an Apple M2 Pro with 16 GiB RAM, macOS arm64,
CPython 3.13.13, uvicorn's wsproto backend, and a release-mode ABI3 extension.

| Microbenchmark | Python ops/s | Rust ops/s | Speedup |
| --- | ---: | ---: | ---: |
| Socket.IO decode | 329,283 | 704,161 | 2.14x |
| Socket.IO encode | 284,489 | 359,069 | 1.26x |
| Engine.IO decode | 254,299 | 803,305 | 3.16x |
| Server receive and dispatch | 250,583 | 396,884 | 1.58x |

The end-to-end medians across three 5-second runs were:

| Variant | Events/s | p50 latency | p95 latency |
| --- | ---: | ---: | ---: |
| Python | 8,728.0 | 10.904 ms | 16.398 ms |
| Rust codecs | 9,858.2 | 9.836 ms | 11.666 ms |

Throughput speedup: **1.129x**. Required threshold: **2.0x**.

## Verdict

Do not rewrite the complete library in Rust based on this prototype. Packet
parsing benefits substantially, but transport and Python dispatch dominate the
real request path. Keep the codec work as an experimental branch and profile
connection state, queueing, and handler dispatch before considering a larger
native core.
