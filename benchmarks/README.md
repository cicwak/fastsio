# Rust Engine.IO experiment

This benchmark is the go/no-go test for rewriting more of fastsio's Engine.IO
stack in Rust. The decision threshold is at least 2x the end-to-end throughput
of the Python baseline under the same workload.

## Scope

The implementation has three optimization levels measured by the suite:

- `rust-codec`: PyO3 Engine.IO and Socket.IO packet codecs under the existing
  python-engineio transport;
- `native-python`: Rust transport/state/protocol with a real Python async
  acknowledgement handler through PyO3;
- `native-core`: the transport ceiling with the acknowledgement handler in Rust.

`NativeServer` uses Socketioxide, Engineioxide, Tokio, and Axum. It currently
supports sync/async Python handlers, typed injection, Pydantic validation,
acknowledgements, connect/disconnect, namespaces, rooms, and emits. Binary
payload bridging, middleware, exception handlers, distributed adapters, and
ASGI mounting remain on the parity roadmap.

## Reproduce

```bash
poetry install
poetry run maturin develop --release
cargo build --release --features native-benchmark --bin fastsio-native-benchmark
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

The current end-to-end medians across three 5-second runs were:

| Variant | Events/s | p50 latency | p95 latency |
| --- | ---: | ---: | ---: |
| Python | 9,834.6 | 9.755 ms | 12.230 ms |
| Rust codecs | 9,566.5 | 9.322 ms | 12.336 ms |
| Native transport + Python async handler | 29,332.1 | 3.207 ms | 6.043 ms |
| Native Rust core | 48,714.7 | 1.775 ms | 3.418 ms |

Native Python throughput speedup: **2.983x**. Native core speedup: **4.953x**.
Required threshold: **2.0x**.

## Verdict

Proceed with the native-core architecture. Codec replacement alone is not
materially faster end to end, while moving transport, connection state, queues,
and protocol handling into Rust clears the 2x gate even when application logic
still runs in Python. Preserve the compatible Python server during feature
parity work instead of silently changing its behavior.
