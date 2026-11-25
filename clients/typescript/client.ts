// Generated from contracts/openapi.json (SHA-256 c9c4c7aa5e282d539f4d9d03a54ec5094c6dac9b06994c029242db9e3c4c43f5). Do not edit.

export interface UploadPart {
  content: Blob
  filename: string
}

export type Problem = { "type": string; "title": string; "status": number; "detail": string; "code": string; "request_id": string; "retryable": boolean }

export type Artifact = { "id": string; "media_type": string; "bytes": number; "sha256": string; "expires_at": string }

export type Identity = { "model": string; "model_revision": string; "engine": string; "engine_revision": string }

export type Capabilities = { "service": "agent"; "contract_version": "1.0.0"; "service_revision": string; "models": Array<Model>; "limits": { "max_request_bytes": number; "max_active_turns_per_run": 1; "idempotency_ttl_seconds": number; "run_idle_ttl_seconds": number; "max_output_tokens": number }; "mcp_protocol_version": "2026-07-28" }

export type Model = { "identity": Identity; "features": { "chat": boolean; "chat_stream": boolean; "workflow": boolean; "structured_outputs": boolean } }

export type Health = { "status": "live" | "ready" }

export type Workflow = { "id": string; "revision": string; "source_sha256": string; "executable_sha256": string; "binding_set_sha256": string; "identity": Identity }

export type Workflows = { "workflows": Array<Workflow> }

export type RunRequest = { "workflow_id": string; "workflow_revision": string }

export type TurnRequest = { "expected_revision": number; "input": string }

export type CancelRequest = { "expected_revision": number }

export type Run = { "id": string; "workflow": Workflow; "status": "ready" | "running" | "cancelling" | "completed" | "failed" | "cancelled"; "revision": number; "last_event_seq": number; "state": Record<string, unknown> }

export type TurnAccepted = { "run_id": string; "turn_id": string; "revision": number; "last_event_seq": number }

export type RunStarted = { "type": "run.started"; "seq": number; "revision": number }

export type TurnStarted = { "type": "turn.started"; "seq": number; "turn_id": string; "revision": number }

export type ReplyDelta = { "type": "reply.delta"; "seq": number; "turn_id": string; "delta": string }

export type StateCommitted = { "type": "state.committed"; "seq": number; "turn_id": string; "revision": number; "state": Record<string, unknown> }

export type ActionReceipt = { "type": "action.receipt"; "seq": number; "turn_id": string; "receipt_id": string; "operation_id": string; "status": "recorded" | "succeeded" | "failed" | "unknown" }

export type TurnCompleted = { "type": "turn.completed"; "seq": number; "turn_id": string; "reply": string; "revision": number }

export type RunCompleted = { "type": "run.completed"; "seq": number; "revision": number }

export type RunCancelled = { "type": "run.cancelled"; "seq": number; "revision": number }

export type TurnFailed = { "type": "turn.failed"; "seq": number; "turn_id": string; "problem": Problem; "revision": number }

export type RunFailed = { "type": "run.failed"; "seq": number; "problem": Problem; "revision": number }

export type RunCancelling = { "type": "run.cancelling"; "seq": number; "revision": number }

export type RunEvent = RunStarted | TurnStarted | ReplyDelta | StateCommitted | ActionReceipt | TurnCompleted | RunCompleted | RunCancelled | TurnFailed | RunFailed | RunCancelling

export type ChatRequest = { "model": string; "messages": Array<{ "role": "system" | "user" | "assistant"; "content": string }>; "stream"?: boolean; "max_completion_tokens"?: number }

export type ChatResponse = { "id": string; "object": "chat.completion"; "created": number; "model": string; "choices": Array<{ "index": 0; "message": { "role": "assistant"; "content": string }; "finish_reason": "stop" | "length" }>; "usage"?: { "prompt_tokens": number; "completion_tokens": number; "total_tokens": number } }

export type ChatChunk = { "id": string; "object": "chat.completion.chunk"; "created": number; "model": string; "choices": Array<{ "index": 0; "delta": { "role"?: "assistant"; "content"?: string }; "finish_reason": "stop" | "length" | null }> }

export type RunRequestTool = { "idempotency_key": string; "workflow_id": string; "workflow_revision": string }

export type TurnRequestTool = { "idempotency_key": string; "expected_revision": number; "input": string; "run_id": string }

export type CancelRequestTool = { "idempotency_key": string; "expected_revision": number; "run_id": string }

export type InstalledModel = { "identity": Identity; "enabled": boolean; "state": "disabled" | "loading" | "ready" | "draining" | "failed"; "active_requests": number; "error_code"?: string; "error_detail"?: string; "installation"?: { "installed": boolean; "runtime": "python-venv" | "ollama"; "hardware": "cpu" | "cuda"; "disk_mib": number; "memory_mib": number; "vram_mib"?: number; "disk_state"?: "not_installed" | "partial" | "installed" | "missing"; "environment_present"?: boolean; "model_bytes"?: number; "progress"?: { "phase": "checking" | "installing_environment" | "downloading_weights" | "verifying" | "starting_runtime"; "completed"?: number; "total"?: number; "unit"?: "bytes" | "assets" } } }

export type InstalledModels = { "models": Array<InstalledModel> }

export type ModelEnableRequest = { "enabled": boolean }

export type RuntimeTelemetry = { "samples": Array<Record<string, unknown>>; "endpoints": Array<Record<string, unknown>>; "models": Array<Record<string, unknown>>; "lifecycle": Array<Record<string, unknown>>; "endpoint_options": Array<string>; "model_options": Array<string>; "window_seconds": number; "client_window_seconds": number; "clients": number; "websockets": number; "active_requests": number; "since": number; "host": Record<string, unknown> | null; "health": Record<string, { "score": number; "label": "Excellent" | "Good" | "Nominal" | "Bad" | "Unusable"; "basis": "Estimated" | "Measured + estimate"; "detail": Array<string>; "rss_bytes": number | null; "audio_rtf": number | null; "placement"?: ModelStorage | null }>; "model_storage"?: ModelStorage | null }

export type ModelStorage = { "mount": string; "source": string; "filesystem": string; "free_bytes": number; "total_bytes": number }

export type OllamaRegistrationRequest = { "tag": string; "digest": string }

export type OllamaRegistrationPin = { "tag": string; "digest": string; "engine_revision": string }

export type OllamaCatalogProvider = { "url": string; "engine": "ollama"; "engine_revision": string }

export type OllamaCatalogModel = { "tag": string; "digest": string; "size_bytes": number; "registered_model"?: string }

export type OllamaCatalog = { "provider": OllamaCatalogProvider; "models": Array<OllamaCatalogModel> }

export type HuggingFaceKeyStatus = { "configured": boolean }

export type HuggingFaceKeyRequest = { "token": string }

export class ApiError extends Error {
  readonly status: number
  readonly requestId: string | null

  constructor(readonly response: Response, readonly body: unknown, readonly problem: Problem | null) {
    super(problem?.detail ?? `HTTP ${response.status}`)
    this.name = "ApiError"
    this.status = response.status
    this.requestId = problem?.request_id ?? response.headers.get("X-Request-ID")
  }
}

function parseProblem(value: unknown): Problem | null {
  if (typeof value !== "object" || value === null) return null
  const record = value as Record<string, unknown>
  if (typeof record.status !== "number" || typeof record.retryable !== "boolean") return null
  const fields = ["type", "title", "detail", "code", "request_id"]
  if (!fields.every(key => typeof record[key] === "string")) return null
  return value as Problem
}

type Parameter = readonly [string, string, "path" | "query" | "header"]
type Operation = readonly [
  string, string, boolean, readonly Parameter[], readonly string[], string, "body" | "accept" | null,
]
type CallValues = Record<string, unknown>

const OPERATIONS: Record<string, Operation> = {
  "liveness": [
    "GET",
    "/livez",
    false,
    [],
    [],
    "application/json",
    null
  ],
  "readiness": [
    "GET",
    "/readyz",
    false,
    [],
    [],
    "application/json",
    null
  ],
  "capabilities": [
    "GET",
    "/v1/capabilities",
    true,
    [],
    [],
    "application/json",
    null
  ],
  "listWorkflows": [
    "GET",
    "/v1/workflows",
    true,
    [],
    [],
    "application/json",
    null
  ],
  "createRun": [
    "POST",
    "/v1/runs",
    true,
    [
      [
        "idempotencyKey",
        "Idempotency-Key",
        "header"
      ]
    ],
    [
      "application/json"
    ],
    "application/json",
    null
  ],
  "getRun": [
    "GET",
    "/v1/runs/{run_id}",
    true,
    [
      [
        "runId",
        "run_id",
        "path"
      ]
    ],
    [],
    "application/json",
    null
  ],
  "submitTurn": [
    "POST",
    "/v1/runs/{run_id}/turns",
    true,
    [
      [
        "runId",
        "run_id",
        "path"
      ],
      [
        "idempotencyKey",
        "Idempotency-Key",
        "header"
      ]
    ],
    [
      "application/json"
    ],
    "application/json",
    null
  ],
  "cancelRun": [
    "POST",
    "/v1/runs/{run_id}/cancel",
    true,
    [
      [
        "runId",
        "run_id",
        "path"
      ],
      [
        "idempotencyKey",
        "Idempotency-Key",
        "header"
      ]
    ],
    [
      "application/json"
    ],
    "application/json",
    null
  ],
  "streamRun": [
    "GET",
    "/v1/runs/{run_id}/events",
    true,
    [
      [
        "runId",
        "run_id",
        "path"
      ],
      [
        "lastEventId",
        "Last-Event-ID",
        "header"
      ]
    ],
    [],
    "text/event-stream",
    "accept"
  ],
  "chatCompletion": [
    "POST",
    "/v1/chat/completions",
    true,
    [],
    [
      "application/json"
    ],
    "application/json",
    "body"
  ],
  "getHuggingFaceKeyStatus": [
    "GET",
    "/v1/control/huggingface",
    true,
    [],
    [],
    "application/json",
    null
  ],
  "saveHuggingFaceKey": [
    "PUT",
    "/v1/control/huggingface",
    true,
    [],
    [
      "application/json"
    ],
    "application/json",
    null
  ],
  "removeHuggingFaceKey": [
    "DELETE",
    "/v1/control/huggingface",
    true,
    [],
    [],
    "application/json",
    null
  ],
  "listInstalledModels": [
    "GET",
    "/v1/control/models",
    true,
    [],
    [],
    "application/json",
    null
  ],
  "setInstalledModelEnabled": [
    "PUT",
    "/v1/control/models/{model_id}",
    true,
    [
      [
        "modelId",
        "model_id",
        "path"
      ]
    ],
    [
      "application/json"
    ],
    "application/json",
    null
  ],
  "getRuntimeTelemetry": [
    "GET",
    "/v1/control/runtime",
    true,
    [
      [
        "windowSeconds",
        "window_seconds",
        "query"
      ],
      [
        "model",
        "model",
        "query"
      ],
      [
        "endpoint",
        "endpoint",
        "query"
      ]
    ],
    [],
    "application/json",
    null
  ],
  "listOllamaCatalog": [
    "GET",
    "/v1/control/ollama/catalog",
    true,
    [],
    [],
    "application/json",
    null
  ],
  "registerOllamaModel": [
    "POST",
    "/v1/control/ollama/registrations",
    true,
    [],
    [
      "application/json"
    ],
    "application/json",
    null
  ]
}

export interface ClientOptions {
  token?: string | (() => string | Promise<string>)
  fetch?: typeof globalThis.fetch
}

function pathValue(value: string): string {
  if (["", ".", ".."].includes(value)) throw new Error("A path parameter must contain a resource identifier.")
  return encodeURIComponent(value).replace(/[!'()*]/g, character =>
    "%" + character.charCodeAt(0).toString(16).toUpperCase(),
  )
}

export class VoxrnAgentClient {
  private readonly baseUrl: string
  private readonly fetcher: typeof globalThis.fetch
  private readonly token: ClientOptions["token"]

  constructor(baseUrl: string, options: ClientOptions = {}) {
    const parsed = new URL(baseUrl)
    if (!["https:", "http:"].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash) {
      throw new Error("Use an HTTP(S) base URL without credentials, a query, or a fragment.")
    }
    this.baseUrl = parsed.href.replace(/\/+$/, "")
    this.fetcher = options.fetch ?? ((input, init) => globalThis.fetch(input, init))
    this.token = options.token
  }

  private async request(operation: string, values: CallValues, streaming = false): Promise<Response> {
    const [method, template, auth, parameters, media, accept, streamSelector] = OPERATIONS[operation]
    const headers = new Headers({Accept: streaming ? "text/event-stream" : accept})
    const query = new URLSearchParams()
    let path = template
    for (const [key, name, location] of parameters) {
      const value = values[key]
      if (value === undefined || value === null) continue
      if (location === "path") path = path.replace("{" + name + "}", pathValue(String(value)))
      else if (location === "header") headers.set(name, String(value))
      else query.append(name, String(value))
    }
    if (/\{[^{}]+\}/.test(path)) throw new Error("A required path parameter is missing.")
    if (auth) {
      const token = typeof this.token === "function" ? await this.token() : this.token
      if (token !== undefined) headers.set("Authorization", `Bearer ${token}`)
    }
    let body: BodyInit | undefined
    if (values.body !== undefined && media[0] === "application/json") {
      let data = values.body as Record<string, unknown>
      if (streamSelector === "body") {
        if (streaming) data = {...data, stream: true}
        else if (data.stream) throw new Error("Use the streaming method for a streaming request.")
      }
      body = JSON.stringify(data)
      headers.set("Content-Type", media[0])
    } else if (values.body !== undefined && media[0] === "multipart/form-data") {
      const form = new FormData()
      for (const [name, value] of Object.entries(values.body as Record<string, unknown>)) {
        if (typeof value === "object" && value !== null && "content" in value && "filename" in value) {
          const part = value as UploadPart
          form.append(name, part.content, part.filename)
        } else if (value !== undefined && value !== null) {
          form.append(name, String(value))
        }
      }
      body = form
    } else if (values.body !== undefined) {
      const contentType = String(values.contentType)
      if (!media.includes(contentType)) throw new Error(`Unsupported request Content-Type. Choose from ${media.join(", ")}.`)
      headers.set("Content-Type", contentType)
      body = values.body as BodyInit
    }
    const url = new URL(this.baseUrl + path)
    url.search = query.toString()
    const response = await this.fetcher(url, {
      method, headers, body, signal: values.signal as AbortSignal | undefined,
      redirect: "error", credentials: "omit",
    })
    if (!response.ok) {
      let payload: unknown = await response.text()
      try {
        payload = JSON.parse(payload as string)
      } catch {
        // Retain any non-JSON response for diagnosis.
      }
      throw new ApiError(response, payload, parseProblem(payload))
    }
    return response
  }

  livenessRaw(options: { signal?: AbortSignal } = {}): Promise<Response> {
    return this.request("liveness", options)
  }

  async liveness(options: { signal?: AbortSignal } = {}): Promise<Health> {
    return (await this.livenessRaw(options)).json()
  }

  readinessRaw(options: { signal?: AbortSignal } = {}): Promise<Response> {
    return this.request("readiness", options)
  }

  async readiness(options: { signal?: AbortSignal } = {}): Promise<Health> {
    return (await this.readinessRaw(options)).json()
  }

  capabilitiesRaw(options: { signal?: AbortSignal } = {}): Promise<Response> {
    return this.request("capabilities", options)
  }

  async capabilities(options: { signal?: AbortSignal } = {}): Promise<Capabilities> {
    return (await this.capabilitiesRaw(options)).json()
  }

  listWorkflowsRaw(options: { signal?: AbortSignal } = {}): Promise<Response> {
    return this.request("listWorkflows", options)
  }

  async listWorkflows(options: { signal?: AbortSignal } = {}): Promise<Workflows> {
    return (await this.listWorkflowsRaw(options)).json()
  }

  createRunRaw(options: { idempotencyKey: string; body: RunRequest; signal?: AbortSignal }): Promise<Response> {
    return this.request("createRun", options)
  }

  async createRun(options: { idempotencyKey: string; body: RunRequest; signal?: AbortSignal }): Promise<Run> {
    return (await this.createRunRaw(options)).json()
  }

  getRunRaw(options: { runId: string; signal?: AbortSignal }): Promise<Response> {
    return this.request("getRun", options)
  }

  async getRun(options: { runId: string; signal?: AbortSignal }): Promise<Run> {
    return (await this.getRunRaw(options)).json()
  }

  submitTurnRaw(options: { runId: string; idempotencyKey: string; body: TurnRequest; signal?: AbortSignal }): Promise<Response> {
    return this.request("submitTurn", options)
  }

  async submitTurn(options: { runId: string; idempotencyKey: string; body: TurnRequest; signal?: AbortSignal }): Promise<TurnAccepted> {
    return (await this.submitTurnRaw(options)).json()
  }

  cancelRunRaw(options: { runId: string; idempotencyKey: string; body: CancelRequest; signal?: AbortSignal }): Promise<Response> {
    return this.request("cancelRun", options)
  }

  async cancelRun(options: { runId: string; idempotencyKey: string; body: CancelRequest; signal?: AbortSignal }): Promise<Run> {
    return (await this.cancelRunRaw(options)).json()
  }

  streamRun(options: { runId: string; lastEventId?: string; signal?: AbortSignal }): Promise<Response> {
    return this.request("streamRun", options, true)
  }

  chatCompletionRaw(options: { body: ChatRequest; signal?: AbortSignal }): Promise<Response> {
    return this.request("chatCompletion", options)
  }

  async chatCompletion(options: { body: ChatRequest; signal?: AbortSignal }): Promise<ChatResponse> {
    return (await this.chatCompletionRaw(options)).json()
  }

  chatCompletionStream(options: { body: ChatRequest; signal?: AbortSignal }): Promise<Response> {
    return this.request("chatCompletion", options, true)
  }

  getHuggingFaceKeyStatusRaw(options: { signal?: AbortSignal } = {}): Promise<Response> {
    return this.request("getHuggingFaceKeyStatus", options)
  }

  async getHuggingFaceKeyStatus(options: { signal?: AbortSignal } = {}): Promise<HuggingFaceKeyStatus> {
    return (await this.getHuggingFaceKeyStatusRaw(options)).json()
  }

  saveHuggingFaceKeyRaw(options: { body: HuggingFaceKeyRequest; signal?: AbortSignal }): Promise<Response> {
    return this.request("saveHuggingFaceKey", options)
  }

  async saveHuggingFaceKey(options: { body: HuggingFaceKeyRequest; signal?: AbortSignal }): Promise<HuggingFaceKeyStatus> {
    return (await this.saveHuggingFaceKeyRaw(options)).json()
  }

  removeHuggingFaceKeyRaw(options: { signal?: AbortSignal } = {}): Promise<Response> {
    return this.request("removeHuggingFaceKey", options)
  }

  async removeHuggingFaceKey(options: { signal?: AbortSignal } = {}): Promise<HuggingFaceKeyStatus> {
    return (await this.removeHuggingFaceKeyRaw(options)).json()
  }

  listInstalledModelsRaw(options: { signal?: AbortSignal } = {}): Promise<Response> {
    return this.request("listInstalledModels", options)
  }

  async listInstalledModels(options: { signal?: AbortSignal } = {}): Promise<InstalledModels> {
    return (await this.listInstalledModelsRaw(options)).json()
  }

  setInstalledModelEnabledRaw(options: { modelId: string; body: ModelEnableRequest; signal?: AbortSignal }): Promise<Response> {
    return this.request("setInstalledModelEnabled", options)
  }

  async setInstalledModelEnabled(options: { modelId: string; body: ModelEnableRequest; signal?: AbortSignal }): Promise<InstalledModel> {
    return (await this.setInstalledModelEnabledRaw(options)).json()
  }

  getRuntimeTelemetryRaw(options: { windowSeconds?: 60 | 300 | 900; model?: string; endpoint?: string; signal?: AbortSignal } = {}): Promise<Response> {
    return this.request("getRuntimeTelemetry", options)
  }

  async getRuntimeTelemetry(options: { windowSeconds?: 60 | 300 | 900; model?: string; endpoint?: string; signal?: AbortSignal } = {}): Promise<RuntimeTelemetry> {
    return (await this.getRuntimeTelemetryRaw(options)).json()
  }

  listOllamaCatalogRaw(options: { signal?: AbortSignal } = {}): Promise<Response> {
    return this.request("listOllamaCatalog", options)
  }

  async listOllamaCatalog(options: { signal?: AbortSignal } = {}): Promise<OllamaCatalog> {
    return (await this.listOllamaCatalogRaw(options)).json()
  }

  registerOllamaModelRaw(options: { body: OllamaRegistrationRequest; signal?: AbortSignal }): Promise<Response> {
    return this.request("registerOllamaModel", options)
  }

  async registerOllamaModel(options: { body: OllamaRegistrationRequest; signal?: AbortSignal }): Promise<InstalledModel> {
    return (await this.registerOllamaModelRaw(options)).json()
  }
}
