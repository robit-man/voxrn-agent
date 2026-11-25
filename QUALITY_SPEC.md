# VoxRN Agent operational quality specification

The ASR design review supplies the baseline. Apply the shared acceptance criteria to each service using its actual data plane and ownership model. Report contract conformance, observed runtime behavior and scale evidence independently.

## Service invariants

- Exposed transports. Buffered and SSE chat, durable workflow creation and turns, event replay, and JSON MCP tools.
- Lifecycle evidence. Compare full serialized buffered and streaming provider envelopes. Verify terminal SSE, Last-Event-ID replay, idempotency, durable state, exact-run identity, and typed action receipts. Preserve input, saved authority and executor scope in all evaluated paths.
- Asynchronous work. Use the existing durable run model for turns and extend job evidence where the same source, provider identity and admitted operations can be retained. Preserve exact source and authority through recovery.
- Credential controls. Separate inference credentials from model management. Keep workflow runs and execution receipts bound to their admitted principal and tenant. Prevent reconnect from widening workflow authority.

## Acceptance matrix

| Requirement | Acceptance evidence |
| --- | --- |
| Conformance in CI | A clean checkout verifies canonical OpenAPI, AsyncAPI where present, MCP contracts, example payloads, generated Pages, and actual route registration. Run the isolated runtime suite on supported Python versions. Exercise authorization, invalid input, cancellation, finality, resource refusal and an admitted happy path. |
| Compatibility | Compare the candidate with the base revision and show removed paths, operations, statuses, required fields, scope changes, or changed protocol identities. Record approved breaking changes and their migration. Generated clients tolerate additive response fields. |
| Usage and telemetry | Version the metric fields and units. Attribute work to the resolved model, endpoint and final outcome. Distinguish accepted, completed, failed, cancelled and disconnected work. Report load, queue, first output, finalization, observed audio real-time factor where applicable, worker residency and lifecycle events. Expose live operator metrics through management scope. Persistent or billing-grade accounting needs additional durable records and reconciliation. |
| Backpressure and heartbeat | Document protocol and transport thresholds, how overload surfaces, and what is discarded, retained, or retried. Capture stalled reader, bounded queue, idle, cancellation and abrupt disconnect cases. Report timing from the real native transport. |
| Python and TypeScript clients | Produce checked-in clients from canonical operations. Validate URL path encoding, request media types, error propagation, raw binary or stream access and stable authentication. Regenerate them in CI. |
| Credential safety | Credential validity, privileges, expiry, revocation and transport bindings have typed negative tests. Cap concurrency and compute by authenticated principal independently of global admission. Keep credentials out of telemetry, trace attributes and diagnostic links. |
| Trace interoperability | Carry valid W3C trace context and correlate local HTTP, streaming, MCP, provider and durable execution receipts. Prove the same trace with a collector before claiming distributed export. |
| Distributed admission | Demonstrate leased placement, queue and resource reservation with at least two independently supervised workers. Verify drain, failed-node recovery, replay safety and tenant isolation. Local-only execution is reported separately in the evidence. |
| Asynchronous work | Accept long-running work with a typed job and idempotency semantics. Record retries, deadlines, cancellation, expiry and version-pinned provenance through terminal readback. |
| Shared schema identity | Reuse identity and event shapes where the semantics match. Keep protocol-specific media framing and state transitions explicit, and validate independently generated tooling against the canonical forms. |
| Protocol developer experience | Publish runnable examples, a protocol lifecycle diagram and a client matrix with tested runtime versions and modes. Link raw generated contracts for machine discovery. |
| Benchmarks and operational objectives | Publish the runtime and model pins, hardware, fixture hashes, concurrency steps, warm and cold samples, latency distributions, resource usage and raw failure classifications. Define measurement windows and success denominators. Establish service objectives only after real repeatable measurements. |

## Current operator metrics contract

`GET /v1/control/runtime` is authenticated by the same management scope as model control. It provides bounded per-process samples and events for the last 60, 300, or 900 seconds. Canonical route names carry no media IDs, run IDs, full URLs or inputs. Current load, recent authenticated clients, and open WebSockets have distinct definitions. The dashboard estimates resource fit from pinned budgets, current storage and memory headroom, host CPU pressure and available runtime measurements. Audio real-time factor is measured inference time divided by processed audio duration. Forecast scores are labeled as estimates until observed evidence exists.

The first release gates are contract validation, generated artifact consistency, runtime conformance and benchmark reproducibility. A code merge or healthy listener alone does not establish performance, reliability or multi-host qualification.

## HTTP trace correlation

A valid version-zero W3C `traceparent` keeps its trace ID and receives a new service span ID. The response exposes this value beside `X-Request-ID`. A malformed or duplicate tracing header creates a fresh trace. The runtime carries the context within the request and omits vendor baggage and credentials. Collector export and durable cross-job tracing require separate verification. [W3C Trace Context](https://www.w3.org/TR/trace-context/)

## Admission limits

Set VOXRN_PER_PRINCIPAL_SLOTS to cap concurrent inference for an authenticated principal within the service-wide slot limit. If omitted, the existing global limit also bounds each principal. The runtime returns 429 with Retry-After and a principal-specific capacity code when a caller exceeds its allocation while global slots remain. A slot is released when its admitted physical work exits. Token- or audio-second budgets require separate metered admission.

## Ollama catalog interoperability

The management API lists already-installed models from the configured Ollama provider. An operator can register a catalog entry by its exact tag and observed digest. Registration leaves download, residency and enablement under the normal model control policy. Imported profiles preserve the actual provider version and immutable digest in responses. A changed upstream tag fails identity verification before inference. Imported entries may serve stateless chat. Admission to a durable workflow still requires its exact bundle identity and declared capabilities.

The API accepts a tag and digest from an observed catalog. Provider addresses come from deployment configuration. A passing check verifies catalog discovery, unauthorized and stale-digest refusal, load state, one raw provider call and exact model provenance. The dashboard shows the configured provider and optional registration action while preserving locally provisioned profiles.

## Model placement storage

The dashboard reports the filesystem containing the managed Ollama weight directory, with its mount source and available and total bytes. This measurement describes the configured local installation. The catalog reports imported model sizes using the configured provider inventory.
