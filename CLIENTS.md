# Generated HTTP clients

The Python and TypeScript clients follow the [canonical OpenAPI operations](contracts/openapi.json). Python uses HTTPX. TypeScript uses Fetch, FormData, and Web Streams. Clients accept a bearer token or a callable that returns a fresh token. Unauthenticated health probes omit credentials. Configure base URLs with any gateway path prefix, for example `https://gateway.example/voxrn`.

| Client | Verified version | Modes covered |
| --- | --- | --- |
| Python | CPython 3.12.3 with HTTPX 0.28.1 | Synchronous HTTP, typed problem errors, raw response access, buffered chat, SSE and run headers |
| TypeScript | TypeScript 5.9.3 and Node 24.14.0 | Fetch, typed problem errors, raw response access, AbortSignal, buffered chat, SSE and run headers |

Install the Python SDK from your checkout.

```sh
python -m pip install ./clients/python
```

Import the TypeScript SDK from `clients/typescript/client.ts`. Type-check it with `tsc -p clients/typescript/tsconfig.json`. Passing `signal` to a TypeScript call allows clients to cancel the request or subsequent response reads.

Typed results leave additional response fields intact. An HTTP error raises `ApiError` with the HTTP status, response, request ID, and original body. Its `problem` property contains the parsed VoxRN problem when the response includes the canonical fields. Methods ending in `_raw` or `Raw` expose status and provenance headers on successful requests. Python releases streamed responses when their context manager exits. TypeScript returns an unread Fetch `Response` for streaming calls.

Regenerate after modifying the canonical contracts.

```sh
python3 scripts/generate_clients.py
python3 scripts/generate_clients.py --check
python3 -m unittest discover -s tests -p test_clients.py -v
```

## Buffered and streamed chat

Set `VOXRN_URL`, `VOXRN_TOKEN`, and `VOXRN_MODEL` to your service URL, scoped bearer credential, and an available model.

```python
from os import environ
from voxrn_agent_sdk import ChatRequest, VoxrnAgentClient

body: ChatRequest = {
    "model": environ["VOXRN_MODEL"],
    "messages": [{"role": "user", "content": "Where is Lisbon?"}],
}
with VoxrnAgentClient(environ["VOXRN_URL"], token=environ["VOXRN_TOKEN"]) as client:
    result = client.chat_completion(body=body)
    print(result["choices"][0]["message"]["content"])
    with client.chat_completion_stream(body=body) as response:
        for line in response.iter_lines():
            if line:
                print(line)
```

`chatCompletionStream` also sets `stream: true` on the request without modifying the caller's messages or options.

```typescript
import { VoxrnAgentClient } from "./clients/typescript/client"

export async function streamChat(baseUrl: string, token: string, model: string) {
  const client = new VoxrnAgentClient(baseUrl, {token})
  return client.chatCompletionStream({
    body: {model, messages: [{role: "user", content: "Where is Lisbon?"}]},
  })
}
```

For durable workflows, use `create_run`, `submit_turn`, and `cancel_run` with stable idempotency keys for retries. Use `stream_run(run_id=..., last_event_id=...)` or `streamRun({runId, lastEventId})` to resume events at the last processed event ID.
