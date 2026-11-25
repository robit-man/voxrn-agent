# VoxRN Agent model validation

The standalone service was exercised on an isolated Linux x86-64 CPU runner on November 25, 2026 UTC. Ollama 0.40.18 served the exact tag/digest pairs in the [installation profiles](scripts/model-profiles.json) with thinking disabled. Qwen3.5-4B used a 32,768-token context. Qwen3.6-27B and Gemma 4 26B used an 8,192-token context.

The requests used the standalone HTTP API and its durable worker. A local trace proxy captured the actual upstream HTTP bodies and terminal responses. Each model received its own admitted bundle sealed to its model digest and exact engine revision. All bundles used the same [shipped source and instructions](examples/intake).

- The positive cases asked for Thursday or, with independent wording, Saturday. Each successful run committed the caller's weekday and exactly one native action receipt. That receipt confirms the local request recording.
- The hard negatives explicitly declined recording Friday or Monday. Each successful run returned to `ready` with empty state and no action receipt.
- Buffered and streamed chat returned Lisbon from the same factual question. Qwen3.5-4B also accepted both physical payloads with all 48 ordered messages intact, including the final 23,313-character source entry and its selected-unit and executor fields.
- Real Qwen3.5-4B requests through MCP verified tool listing, model discovery and buffered inference. The MCP inference request was `5edbc6b0ec1a4d8482a42a9935ad59e6`. The resident runner had zero VRAM allocated and unloaded on service shutdown.
- Each candidate was disabled and re-enabled through the HTTP control plane. A new request received `503 model_unavailable` while disabled. Ollama reported no resident runner after each release. The final app shutdown also left no runner resident.
- The legacy `qwen-local` profile remains available for compatibility. Its identical factual question produced Lisbon buffered and Vila Real streamed, and the stream completed normally. The preserved request and terminal packets are described in [issue #2](https://github.com/robit-man/voxrn-agent/issues/2).

The intake source SHA-256 was `a891da802d7ea71cc5c687d77387d66a508f5602e3ac45526707ddc2f2317b49`. Its instructions SHA-256 was `b7eb413d393005e709c100c67654e21b6ccdd5628ec7b44f8d7e769a74280f1a`. The caller-evidence policy now also controls which operations the structured decoder can choose. Its post-inference guard still prevents unauthorized operations if a provider ignores that schema. The original failures and run IDs are retained in [issue #3](https://github.com/robit-man/voxrn-agent/issues/3) and [issue #4](https://github.com/robit-man/voxrn-agent/issues/4).

The twelve captured workflow bodies were checked against the exact bundle instructions, source, typed operations and participant input. All final provider decisions matched the authorized action and exact field values.

## Durable run evidence

| Profile | Case | Run ID | Native receipt ID |
| --- | --- | --- | --- |
| `qwen3.5-4b` | Record Thursday | `c5dd064706884931a3f9e28bc47c3893` | `69def1fb27c740ebbbdaf3a79372b74d` |
| `qwen3.5-4b` | Decline Friday | `17a6f4ff164e449d82658d0e71f62e72` | None |
| `qwen3.5-4b` | Record Saturday | `0014f4acb46b48019f2d9fc0f99d5aa2` | `ba4c304f2db145f096474d6378b835d7` |
| `qwen3.5-4b` | Decline Monday | `9d1747abb6fb45719519d38bd1122db3` | None |
| `qwen3.6-27b` | Record Thursday | `7cade4177b5d4f029f9be69c22620809` | `34a8e9309dc94bccbfdc4376ab29563e` |
| `qwen3.6-27b` | Decline Friday | `694e470471734a3693a2f4c1eeb8e52a` | None |
| `qwen3.6-27b` | Record Saturday | `98427181888c43e989b0e3d8d9676ec2` | `206c109bfdcc4804a51bda0c28ffc1f5` |
| `qwen3.6-27b` | Decline Monday | `73086ad2107a4381a1e726ed1d11d867` | None |
| `gemma4-26b` | Record Thursday | `3b530be7ba1f409c8b05b7dd26e70bc1` | `0b0aa7f3151a4bc2b5d2de12ba1f1467` |
| `gemma4-26b` | Decline Friday | `b90cb90a38704711baff737e93c3d571` | None |
| `gemma4-26b` | Record Saturday | `2274984825774d42840b7a718f6347eb` | `095f30f2e69848ad9daa8ce9d7679213` |
| `gemma4-26b` | Decline Monday | `33b66710129a417198df2a45f1e8b732` | None |

This evidence covers the pinned standalone chat and local request-recording workflows. Full Voryn workflows, live calls, external execution, hardware-specific performance and concurrent load require their own verification.
