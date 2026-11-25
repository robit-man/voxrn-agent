# Buffered inference benchmark

Run on 2026-11-25 UTC with native Linux 6.11.0-29-generic on an AMD Ryzen Threadripper 7960X and 48 logical CPUs. Each service had its own temporary loopback installation. Tests shared the same physical host. Each level sent four synchronized waves without retries. Timings cover HTTP request through complete HTTP response.

Model qwen-local at 7df6b6e09427a769808717c0a93cadc4ae99ed4eb8bf5ca557c90846becea435, engine ollama 0.40.18. Fixture SHA-256 6ab6eebea81bd9ff8774723f6abc0413a6056220003c64eb443694ee3dde9dbc.

## Reproduce

Enable the pinned profile and run the harness.

```sh
python3 scripts/benchmark.py --url http://127.0.0.1:8313 \
  --settings "$HOME/.local/share/voxrn/agent/settings.json" \
  --concurrency 1 2 4 --waves 4 --output /tmp/voxrn-agent-benchmark.json
```

The arithmetic fixture requests a one-digit answer. The original baseline and physical replay used a permissive numeral-presence predicate. That predicate accepted an answer beginning with 1 because its explanation contained five. The harness now requires exactly 5 after whitespace trimming. This stricter check was applied to the replay's retained replies. The original baseline recorded IDs and sizes for its requests.

The physical replay captured 21 native provider completions including the initial request. All stopped normally. Four returned a correct equation in the wrong format. The rest led with 1, 2 or 3. Zero fulfilled the original prompt. The preserved results show a model-output failure independent of admission. [Legacy model issue](https://github.com/robit-man/voxrn-agent/issues/2)

## Observed load

| Concurrent | Attempts | HTTP 200 | 429 | Fixture passes | p50 admitted | p95 admitted |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 4 | 4 | 0 | 0 | 35.39 ms | 38.99 ms |
| 2 | 8 | 8 | 0 | 0 | 104.41 ms | 220.77 ms |
| 4 | 16 | 8 | 8 | 0 | 94.83 ms | 109.52 ms |

Admission refusals and completed fixture failures have separate counts. Percentiles describe one short workload with small samples. Streaming, sustained capacity and multi-host behavior require separate measurement.

[Raw baseline receipts](benchmarks/baseline-2026-11-25.json) · [Captured replay receipts](benchmarks/baseline-2026-11-25-physical-replay.json)
