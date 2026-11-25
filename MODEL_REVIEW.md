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

## Local Ollama artifact evidence

The official registry returned exact Q4_K_M manifests for the five local candidates on 2026-11-25. Anonymous HEAD responses for each largest model layer matched its declared byte length. The tags and full manifest digests below identify the published conversion selected by VoxRN. The local managed engine is pinned to Ollama 0.40.18.

| VoxRN model | Ollama tag | Manifest SHA-256 | All layers |
| --- | --- | --- | ---: |
| `qwen3.5-4b` | [qwen3.5:4b](https://ollama.com/library/qwen3.5:4b) | `d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04` | 3,324,173,757 bytes |
| `qwen3.6-27b` | [qwen3.6:27b](https://ollama.com/library/qwen3.6:27b) | `bcbdbd4bcf86f2c4a56cd1698fb31f9434f7dd99752f71b636c750aaa67a49a5` | 17,769,076,719 bytes |
| `gemma4-26b` | [gemma4:26b](https://ollama.com/library/gemma4:26b) | `001e5dafc3c77684c2307ebc6ab8e336e10c9b18eca52acf547d72fc83c3ca8c` | 18,731,025,387 bytes |
| `qwen3.6-35b` | [qwen3.6:35b](https://ollama.com/library/qwen3.6:35b) | `a7eb95c53bcf96b4bdd008d0fab4a5dac88047d9c1a7a9ab88ed453423fbd87c` | 22,621,314,161 bytes |
| `gemma4-12b` | [gemma4:12b](https://ollama.com/library/gemma4:12b) | `6114515d63c17436a7c0417d82820ac65ad643e2806c5a3c89cb62846436ed0b` | 8,021,618,699 bytes |

DeepSeek V4.1 Flash remains a data-center candidate. The official `cloud` and `1017-cloud` tags resolve to manifest `4ac3801a8d7e4cfdbc899090a136adf81dff821ccc600e0c9653e275000127ae` with zero local weight layers. The exact native revision `314113f8a7dadb388215396ba5fbee2c610a04d6` contains 48 safetensors shards totaling 510,296,708,312 bytes. That exceeds the test host's 377 GiB physical RAM. A local run requires an independent backend and a host sized for the checkpoint. Managed Ollama disables cloud inference. [Ollama listing](https://ollama.com/library/deepseek-v4.1-flash) · [Exact native weights](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/tree/314113f8a7dadb388215396ba5fbee2c610a04d6).

## Standalone CPU verification

Five local candidates ran their exact pinned manifests on Ollama 0.40.18 in a disposable managed installation. The HTTP fixture exercised retained chat history in buffered and streaming modes. A separately worded MCP fixture checked whether the model preserved a stated restriction. Each result returned the expected answer, completed normally, and the subsequent disable removed the model from Ollama's active runners. These are transport and basic input-fidelity checks.

| Profile | Buffered request | Streaming request | MCP result |
| --- | --- | --- | --- |
| `qwen3.5-4b` | `ca085087f86345ddb3d7a8d7c1a6f910` | `a277a65ad32f48ac88b22e32bf7cd619` | `fe002bb1605241e4b9c72f6a9a400728` |
| `gemma4-12b` | `b555ffd08bd74f83b7dc7e9c2219eb71` | `b5390a17876e45609b069c2f05acb425` | `66e3033b019042abb8dfa1b4f663f42e` |
| `qwen3.6-35b` | `f573f6e3e4a640b6a167e94947dc8256` | `7c3cd6f707d74a728d23176954fe9200` | `fa9796a42b354bc6bffc12ac2694d0e4` |
| `qwen3.6-27b` | `30870fa7ff2d41c5b6ff045378626c69` | `7f45c019f09b446fac0383ae8286c51c` | `692f7a693af843c6870f0d449f187fc5` |
| `gemma4-26b` | `b243dc367b5b42c4adedbecb1ef95315` | `34457cd2b6a94e7194e33cd6d840fba6` | `a78674d6134e4980b55984fb0f5c35c9` |

An unchanged Gemma 4 12B replay verified the managed provider registry repair against a prefix provisioned before the model was added. A separate download controller reached its deadline while the last large tag was still staging and stopped the isolated provider during an initial Qwen3.6 35B run. The retained pull and release logs identify that interruption. The five receipts above came from completed requests on the pinned local provider after confirmable tag and runner checks. [Managed upgrade issue](https://github.com/robit-man/voxrn-agent/issues/7).
