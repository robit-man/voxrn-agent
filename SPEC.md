# Agent inference and execution service v1

## Boundary and starting point

Own a stateless text inference adapter and a durable runtime for admitted, versioned workflows. Admit a finalized participant turn against a pinned workflow. Assemble provider messages losslessly, validate model outputs, execute only declared operations under the initialized run's authority, and publish attributable state and action receipts. The platform owns workflow authoring, profile UI, accounts, telephony, media, saved artifact selection and transport playback. Standalone deployments can provision admitted workflows and tools directly without a Voryn instance.

The source baseline is [Voryn aa4bb0c1](https://github.com/robit-man/voryn/tree/aa4bb0c1). `lib/enterpriseAgentService.js` owns hosted buffered and streaming inference and drives the call runtime. `lib/enterpriseInferenceProviders.js` and `lib/callTreeStructuredInferenceTransport.js` cover provider normalization and structured inference. `lib/agentWorkflows/runtime.js` and adjacent compiler, executable and bound-operation modules carry source authority, pinned executable identities and operation semantics. `lib/aiAgentCallTrees.js` provides `initializeEnterpriseTaskCallRun` and the task state boundary. `lib/savedAgentCallDispatch.js` is shared by saved phone and local-media dispatch. Preserve the existing Voryn initialization and platform state writer until a separately verified cutover transfers that ownership. The production-backed counterpart trial, raw media peer/client, and phone transport are consumers of shared agent behavior.

Start with the deployed Ollama-backed adapter and the same configured model and message path. Separate the orchestration core from Next.js, carrier and Voryn storage. Package a fresh service catalogue, durable store migration and pre-bound executor registry. A local developer profile can serve through an explicitly provisioned external provider and a standalone durable store. A production profile uses transactional state and a durable work queue with leased recovery. Store provider credentials on the server. Pin and drain run ownership by revision during rollout.

## Stateless text inference

`POST /v1/chat/completions` implements a text-only, one-choice subset of Chat Completions. Use ordered `system`, `user` and `assistant` messages with string content. Set `stream: true` for SSE, otherwise expect JSON. `max_completion_tokens` is an explicit output bound; omission uses the configured, advertised limit. The effective model ID is in the response, with model and engine revisions in headers. Reject unknown request fields and roles, unsupported model features or oversized context with a typed problem. Text chat has no live tools, response storage or workflow side effects. Structured output is used inside declared workflows when the pinned model supports it. A schema-enforcement flag in the catalog describes that ability; it grants no additional chat parameters.

```sh
curl "$SERVICE_URL/v1/chat/completions" \
  -H "Authorization: Bearer $SERVICE_TOKEN" \
  -H 'Content-Type: application/json' \
  --data '{"model":"qwen-local","messages":[{"role":"user","content":"Summarize this in one sentence."}],"stream":false}'
```

Chat SSE carries data-only events. Use a role and text delta stream with one final choice containing a nonnull `finish_reason`, then send `data: [DONE]`. Cancellation on disconnect and failure after headers terminate the stream without the completion marker. HTTP problems follow the shared service contract. A compatibility adapter for other Chat Completions fields or the Responses API requires its own client and provider conformance checks. [vLLM compatibility surface](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/) and [Ollama Chat API](https://docs.ollama.com/api/chat).

## Durable workflow interface

`GET /v1/workflows` exposes permitted, immutable workflow revisions with source, executable, and binding-set SHA-256 digests and exact model/engine identity. The runtime catalog admits a workflow only after validating its source projection, typed state and operation schemas, executable ABI, binding configuration, and allowed tool capabilities. Install revisions through the operator's deployment process. Credentials and executor URLs do not enter the portable definition or client requests. [Workflow bundle schema](contracts/workflow-bundle.schema.json) defines the standalone intake envelope.

An installed bundle resolves its trusted entry point and ABI against a preconfigured runtime registry. Verify its artifact digests and binding-set digest before making it discoverable. Each operation declares a stable ID, binding ID, effect class, and input/output schemas. A binding must have explicit approval for that operation and effect. Runtime admission rejects unknown entry points, unbound operations, unrecognized ABI, invalid initial state, and references outside the installed artifacts. Provision source and executable bytes alongside the envelope. Validate JSON schemas without fetching remote references.

1. `POST /v1/runs` with `workflow_id`, `workflow_revision` and an `Idempotency-Key` initializes durable state at revision 0 and appends `run.started` before the service accepts turns. It returns 201 and a run location.
2. `POST /v1/runs/{run_id}/turns` accepts `input` and `expected_revision` plus an idempotency key. A 202 means the final participant input and the worker intent are durable. The state becomes running and its revision advances. Only one turn may be active. Requests against stale or terminal state return 409, unless they exactly replay a retained key.
3. `GET /v1/runs/{run_id}` returns the current committed snapshot. `GET /v1/runs/{run_id}/events` streams the durable event ledger. Use the decimal `Last-Event-ID` header to replay strictly after a known sequence, or omit it to start from the first retained event. A future sequence returns 409 and an expired cursor returns 410. Subscribe with `Accept: text/event-stream`. Use `event: <type>`, `id: <seq>`, and one JSON `data:` line for each durable event and SSE comments for heartbeats. A terminal run event ends the stream. A connection-only failure closes the stream without advancing the durable cursor. Reattach using the last verified durable sequence. Deltas may be provisional. `turn.completed` contains the finalized reply. Only a `state.committed` event carries a committed state change.
4. `POST /v1/runs/{run_id}/cancel` accepts the expected revision and an idempotency key. A 202 records cancellation intent. The run enters `cancelling` until fenced owners report the resolved status. Cancellation stops future work and stale commits under a writer fence. If a side effect has already dispatched, persist its actual receipt or an unknown outcome before closing the run. Its intent or a cancelled run status cannot establish external success, reversal, or safe retry.

A turn finishes at `turn.completed` or `turn.failed`. After a completed turn the workflow is ready for the next input or emits `run.completed` when all its required duties reach an authorized terminal. If cancellation interrupts a turn, the service emits `turn.failed` with `operator_cancelled` then `run.cancelled`. A substantive failed turn closes the run with `run.failed` in v1. Retain committed state and receipts on any failure. The per-run revision increases on each committed change in public run state, including admission and lifecycle transitions; it is independent from event sequence.

Key retries are scoped to principal, operation, target and key, and bound to the canonical request body using RFC 8785. Replay an identical key with the original response and status without dispatching new work, even if the run has advanced. A key reused with a different body returns 409. Lookup precedes revision checking. Respect the published key retention window. After losing a response and the key window, reconcile from the run and operation receipts before resubmitting. Bind each action to a stable operation identity and local intent record. Cross-system retries require an executor with an idempotency mechanism or an authoritative readback. A crash after dispatch with unknown external state requires reconciliation.

```sh
curl "$SERVICE_URL/v1/runs" \
  -H "Authorization: Bearer $SERVICE_TOKEN" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: example-create-1' \
  --data '{"workflow_id":"intake","workflow_revision":"1.0.0"}'
curl "$SERVICE_URL/v1/runs/$RUN_ID/turns" \
  -H "Authorization: Bearer $SERVICE_TOKEN" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: example-turn-1' \
  --data '{"expected_revision":0,"input":"Please record the request for Friday."}'
curl --no-buffer "$SERVICE_URL/v1/runs/$RUN_ID/events" \
  -H "Authorization: Bearer $SERVICE_TOKEN" -H 'Accept: text/event-stream'
```

## MCP and federation

Use `agent_capabilities`, `agent_list_workflows`, and `agent_get_run` for discovery and authorized readback. `agent_infer` invokes the buffered stateless text subset. `agent_create_run`, `agent_submit_turn` and `agent_cancel_run` map directly to the HTTP commands and accept `idempotency_key` as a tool argument. Scope controls whether the caller can access a workflow or run, and a deployment can issue read-only credentials. Tool action scope never grants access to additional bound executors. Tool execution uses the same state, idempotency, operation and evidence paths as HTTP. Use the HTTP event feed for event replay or live subscriptions. Run submission returns an admission receipt rather than waiting for a dialogue or tool loop to complete.

Consider an A2A 1.0 adapter for service-to-service delegation. Its Agent Card, message/streaming tasks, result and cancellation operations can wrap the run lifecycle behind an independent authorization boundary. Add it only after validating the mapping from task/message IDs to run/turn IDs, cancellation and action receipts. V1 does not advertise A2A compatibility. [A2A specification](https://a2a-protocol.org/latest/specification/).

## Admission, provider parity and release evidence

- Preserve source, selected unit, bindings, settings and ordered message history. Compare byte-exact provider requests for buffered and streaming modes with long inputs and retained history. Provider-specific schema translation and role rules must be explicit, reversible where possible, and exercised at the adapter boundary. Reject capacity overflow instead of slicing, dropping, or inventing content. See the [physical-context RCA](https://github.com/robit-man/voryn/blob/aa4bb0c1/docs/voice-agent/HOSTED_CONTEXT_TRUNCATION_RCA.md).
- Validate structured outputs before writing state. Authorize every action by source structure and a pinned typed binding. Keep native recording, verified external execution, failure and unresolved outcomes separately evidenced. Restrictions on dependencies never replace the saved assignment, or discard enabled native task fulfillment.
- Preserve failure inputs and causal receipts. Compare the deployed request, provider response, protocol validation, retry and actual durable state. Verify approved long-context, successful, and hard-negative cases against a pinned baseline, including retained failures and independently authored held-out inputs. Cover stale revisions and keys, races, worker restart, unknown external effects, cancellation, and replay.
- Use assigned calls, dashboard simulation, phone dry runs and audible trials against the same saved profile, tree, bindings and shared runtime. Capture real serialized provider requests, committed state, verified executor receipts, and delivered audio independently. Preserve the browser's actual completion signal. No caller coaching or test-only inference path. Unknown differences block a parity claim. [Voryn parity contract](https://github.com/robit-man/voryn/blob/aa4bb0c1/docs/voice-agent/PRODUCTION_TEST_PARITY.md).
- Retain the current provider as the reference and evaluate a pinned vLLM adapter with a comparable Qwen checkpoint and an independent open-weight candidate such as gpt-oss. Use schema conformance, citation and source fidelity, exact field/branch decisions, tool outcome authority and latency/capacity per workload. Check output contracts and reasoning versus final-answer handling specific to each provider. Record hardware, configuration, weights and engine revision. [vLLM structured outputs](https://docs.vllm.ai/en/latest/features/structured_outputs/) and [gpt-oss model release](https://openai.com/index/introducing-gpt-oss/).
