# Chat and workflow streams

For chat, send `POST /v1/chat/completions` with `stream: true` and a credential with `agent:infer`. The service waits up to 90 seconds for the first provider packet so it can return a typed HTTP problem before opening a stream. After it opens, a 10-second idle window produces an SSE comment `: heartbeat` while the next application frame is pending. Data frames retain the OpenAI-compatible role, content and finish-reason fields. Require a finish reason and `data: [DONE]` for success.

The publisher pulls one frame at a time and awaits downstream flow. A send blocked for 15 seconds aborts the response and cancels the provider iterator. The service also cancels on client disconnect during preflight or streaming. It releases the admission and exact model lease after cleanup. The post-open provider stream has a separate 120-second deadline. Treat an interrupted chat as incomplete. Chat chunks are transient, so a new request begins new generation.

For workflow events, request `GET /v1/runs/{run_id}/events` with `Accept: text/event-stream` and `agent:read`. Supply `Last-Event-ID` to recover after a disconnect. Each poll retrieves at most 64 durable rows. Idle comments use the same 10-second interval and carry no event ID. The response uses the same 15-second write deadline. A stalled event reader does not cancel the durable workflow. Use the separate cancellation operation to cancel a run.

Native loopback verification with Uvicorn 0.40.0, Python 3.12 and a synthetic streaming provider received the first chat heartbeat at 10.007 seconds and the next content delta at 10.256 seconds. With an unread TCP socket and an 8 KiB server send buffer, production ended at 15.004 seconds after seven upstream content frames. These are origin-side conformance timings. Proxy buffering and real provider output rates require separate measurements.

```sh
python -m unittest discover -s tests -p test_streaming_policy.py -v
```

The suite covers native chunk order, terminal framing, reader backpressure and abrupt TCP disconnect. Existing workflow conformance covers durable event replay. [OpenAPI contract](contracts/openapi.json).
