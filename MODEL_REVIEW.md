# VoxRN Agent open-model review

Window 2025-11-24 through 2026-11-24. This is a ranked shortlist for source-grounded workflow execution, structured output, and routine chat on self-hosted inference. Scores are estimates from published primary evidence and serving fit. Agent coding results give evidence of tool use, while workflow source fidelity requires a separate test. Weights are available under the terms of the specific checkpoint.

## Candidate ranking

Weights are grounded and structured reasoning evidence 35%, latency and capacity fit 20%, serving and integration fit 30%, and terms/availability 15%. Each dimension is 0–5. Weighted mean is scaled to 100. Runtime scores favor predictable compute in the VoxRN device range. Serving considers native or documented adapters, schema control, streaming, and model-template support.

| Rank | Release window | Candidate | Evidence / Runtime / Serving / Terms | Score | Rationale and primary evidence |
| --- | --- | --- | --- | --- | --- |
| 1 | Apr 2026 | Qwen3.6-27B | 5 / 4 / 5 / 5 | **96** | Apache 2.0 dense workstation/server candidate with strong published agentic evaluations and documented multi-turn reasoning preservation. Test exact provider requests and distinguish private reasoning from structured output. [Qwen release](https://qwen.ai/blog?id=qwen3.6-27b&lang=zh-CN), [checkpoint](https://huggingface.co/Qwen/Qwen3.6-27B) |
| 2 | Mar 2026 | Qwen3.5-4B | 4 / 5 / 5 / 5 | **93** | Apache 2.0 compact candidate for CPU fallback or low-memory acceleration. Validate source fidelity, long schemas and hard negatives before relying on its field or action decisions. [Qwen model card](https://huggingface.co/Qwen/Qwen3.5-4B) |
| 3 | Apr 2026 | Gemma 4 26B-A4B | 5 / 4 / 4 / 5 | **90** | Apache 2.0, independent model family, native agentic support and sparse compute. Check routing at batch size one, parser compatibility, and actual model+KV memory. [Google release](https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/), [model card](https://huggingface.co/google/gemma-4-26b-a4b-it) |
| 4 | Apr 2026 | Qwen3.6-35B-A3B | 4 / 5 / 4 / 5 | **87** | Apache 2.0 candidate for throughput-focused GPU serving. Small active parameter count still requires fitting the complete checkpoint and cache. Compare controlled reasoning budgets with the 27B dense model. [Qwen model card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) |
| 5 | Jun 2026 | Gemma 4 12B | 4 / 4 / 4 / 5 | **83** | Apache 2.0 mid-size checkpoint. Useful local independence check with native audio input available for an explicitly scoped experiment. Maintain the existing ASR boundary for the baseline. [Google release](https://blog.google/innovation-and-ai/technology/developers-tools/introducing-gemma-4-12B/) |
| 6 | Sep 2026 | DeepSeek-V4.1-Flash | 5 / 1 / 3 / 5 | **72** | MIT data-center reference with a strong author-reported reasoning profile. Its hundreds of billions of total parameters and specialized serving requirements explain the ranking for VoxRN's target footprint. [DeepSeek model card and evaluations](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) |

Use Qwen3.5-4B for the first small-machine comparison, Qwen3.6-27B for a workstation qualification, and Gemma 4 26B as the independent comparator. Benchmark the DeepSeek checkpoint only in a hardware tier where its complete weights and cache fit. The installed Qwen3 0.6B CPU model serves as a smoke baseline. See the [standalone runtime evidence](MODEL_VALIDATION.md) for the three checkpoint profiles exercised after this review.

## Paired evaluation

1. Freeze unchanged sources, selected units, assignments, saved profiles and executor bindings. Run the retained failures and an independent case with different wording or layout. Add a positive and a hard negative for every changed contract.
2. Assert exact source and history delivery in buffered and streamed provider bodies. Score valid structured finals, required field and branch correctness, typed operation admission, durable writes, and receipt-backed completion separately. Record truncation and reasoning-only output as failures.
3. Measure TTFT, completed-turn latency and token and memory use on realistic context sizes and concurrency. Report prefill and decoding separately. Compare quantizations against their native or a higher-precision reference on the same workload.
4. Preserve tests of silence, negation, ambiguous values, missing authority and a failed executor. A schema-valid response earns no state or execution credit unless committed readback and verified receipts agree. Record exact weight revision, tokenizer, parser, runtime and serving flags.
