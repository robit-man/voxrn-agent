# Generated from contracts/openapi.json (SHA-256 c9c4c7aa5e282d539f4d9d03a54ec5094c6dac9b06994c029242db9e3c4c43f5). Do not edit.

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Any, BinaryIO, Literal, NotRequired, TypedDict
from urllib.parse import quote, urlsplit

import httpx


@dataclass(frozen=True)
class UploadPart:
    """A multipart file with its original filename and declared media type."""

    content: bytes | BinaryIO
    filename: str
    content_type: str


class Problem(TypedDict):
    type: str
    title: str
    status: int
    detail: str
    code: str
    request_id: str
    retryable: bool


class Artifact(TypedDict):
    id: str
    media_type: str
    bytes: int
    sha256: str
    expires_at: str


class Identity(TypedDict):
    model: str
    model_revision: str
    engine: str
    engine_revision: str


class CapabilitiesLimits(TypedDict):
    max_request_bytes: int
    max_active_turns_per_run: Literal[1]
    idempotency_ttl_seconds: int
    run_idle_ttl_seconds: int
    max_output_tokens: int


class Capabilities(TypedDict):
    service: Literal['agent']
    contract_version: Literal['1.0.0']
    service_revision: str
    models: list[Model]
    limits: CapabilitiesLimits
    mcp_protocol_version: Literal['2026-07-28']


class ModelFeatures(TypedDict):
    chat: bool
    chat_stream: bool
    workflow: bool
    structured_outputs: bool


class Model(TypedDict):
    identity: Identity
    features: ModelFeatures


class Health(TypedDict):
    status: Literal['live', 'ready']


class Workflow(TypedDict):
    id: str
    revision: str
    source_sha256: str
    executable_sha256: str
    binding_set_sha256: str
    identity: Identity


class Workflows(TypedDict):
    workflows: list[Workflow]


class RunRequest(TypedDict):
    workflow_id: str
    workflow_revision: str


class TurnRequest(TypedDict):
    expected_revision: int
    input: str


class CancelRequest(TypedDict):
    expected_revision: int


class Run(TypedDict):
    id: str
    workflow: Workflow
    status: Literal['ready', 'running', 'cancelling', 'completed', 'failed', 'cancelled']
    revision: int
    last_event_seq: int
    state: dict[str, Any]


class TurnAccepted(TypedDict):
    run_id: str
    turn_id: str
    revision: int
    last_event_seq: int


class RunStarted(TypedDict):
    type: Literal['run.started']
    seq: int
    revision: int


class TurnStarted(TypedDict):
    type: Literal['turn.started']
    seq: int
    turn_id: str
    revision: int


class ReplyDelta(TypedDict):
    type: Literal['reply.delta']
    seq: int
    turn_id: str
    delta: str


class StateCommitted(TypedDict):
    type: Literal['state.committed']
    seq: int
    turn_id: str
    revision: int
    state: dict[str, Any]


class ActionReceipt(TypedDict):
    type: Literal['action.receipt']
    seq: int
    turn_id: str
    receipt_id: str
    operation_id: str
    status: Literal['recorded', 'succeeded', 'failed', 'unknown']


class TurnCompleted(TypedDict):
    type: Literal['turn.completed']
    seq: int
    turn_id: str
    reply: str
    revision: int


class RunCompleted(TypedDict):
    type: Literal['run.completed']
    seq: int
    revision: int


class RunCancelled(TypedDict):
    type: Literal['run.cancelled']
    seq: int
    revision: int


class TurnFailed(TypedDict):
    type: Literal['turn.failed']
    seq: int
    turn_id: str
    problem: Problem
    revision: int


class RunFailed(TypedDict):
    type: Literal['run.failed']
    seq: int
    problem: Problem
    revision: int


class RunCancelling(TypedDict):
    type: Literal['run.cancelling']
    seq: int
    revision: int


class ChatRequestMessagesItem(TypedDict):
    role: Literal['system', 'user', 'assistant']
    content: str


class ChatRequest(TypedDict):
    model: str
    messages: list[ChatRequestMessagesItem]
    stream: NotRequired[bool]
    max_completion_tokens: NotRequired[int]


class ChatResponseChoicesItemMessage(TypedDict):
    role: Literal['assistant']
    content: str


class ChatResponseChoicesItem(TypedDict):
    index: Literal[0]
    message: ChatResponseChoicesItemMessage
    finish_reason: Literal['stop', 'length']


class ChatResponseUsage(TypedDict):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatResponse(TypedDict):
    id: str
    object: Literal['chat.completion']
    created: int
    model: str
    choices: list[ChatResponseChoicesItem]
    usage: NotRequired[ChatResponseUsage]


class ChatChunkChoicesItemDelta(TypedDict):
    role: NotRequired[Literal['assistant']]
    content: NotRequired[str]


class ChatChunkChoicesItem(TypedDict):
    index: Literal[0]
    delta: ChatChunkChoicesItemDelta
    finish_reason: Literal['stop', 'length', None]


class ChatChunk(TypedDict):
    id: str
    object: Literal['chat.completion.chunk']
    created: int
    model: str
    choices: list[ChatChunkChoicesItem]


class RunRequestTool(TypedDict):
    idempotency_key: str
    workflow_id: str
    workflow_revision: str


class TurnRequestTool(TypedDict):
    idempotency_key: str
    expected_revision: int
    input: str
    run_id: str


class CancelRequestTool(TypedDict):
    idempotency_key: str
    expected_revision: int
    run_id: str


class InstalledModelInstallationProgress(TypedDict):
    phase: Literal['checking', 'installing_environment', 'downloading_weights', 'verifying', 'starting_runtime']
    completed: NotRequired[int]
    total: NotRequired[int]
    unit: NotRequired[Literal['bytes', 'assets']]


class InstalledModelInstallation(TypedDict):
    installed: bool
    runtime: Literal['python-venv', 'ollama']
    hardware: Literal['cpu', 'cuda']
    disk_mib: int
    memory_mib: int
    vram_mib: NotRequired[int]
    disk_state: NotRequired[Literal['not_installed', 'partial', 'installed', 'missing']]
    environment_present: NotRequired[bool]
    model_bytes: NotRequired[int]
    progress: NotRequired[InstalledModelInstallationProgress]


class InstalledModel(TypedDict):
    identity: Identity
    enabled: bool
    state: Literal['disabled', 'loading', 'ready', 'draining', 'failed']
    active_requests: int
    error_code: NotRequired[str]
    error_detail: NotRequired[str]
    installation: NotRequired[InstalledModelInstallation]


class InstalledModels(TypedDict):
    models: list[InstalledModel]


class ModelEnableRequest(TypedDict):
    enabled: bool


class RuntimeTelemetry(TypedDict):
    samples: list[dict[str, Any]]
    endpoints: list[dict[str, Any]]
    models: list[dict[str, Any]]
    lifecycle: list[dict[str, Any]]
    endpoint_options: list[str]
    model_options: list[str]
    window_seconds: int
    client_window_seconds: int
    clients: int
    websockets: int
    active_requests: int
    since: float
    host: dict[str, Any] | None
    health: dict[str, dict[str, Any]]
    model_storage: NotRequired[ModelStorage | None]


class ModelStorage(TypedDict):
    mount: str
    source: str
    filesystem: str
    free_bytes: int
    total_bytes: int


class OllamaRegistrationRequest(TypedDict):
    tag: str
    digest: str


class OllamaRegistrationPin(TypedDict):
    tag: str
    digest: str
    engine_revision: str


class OllamaCatalogProvider(TypedDict):
    url: str
    engine: Literal['ollama']
    engine_revision: str


class OllamaCatalogModel(TypedDict):
    tag: str
    digest: str
    size_bytes: int
    registered_model: NotRequired[str]


class OllamaCatalog(TypedDict):
    provider: OllamaCatalogProvider
    models: list[OllamaCatalogModel]


class HuggingFaceKeyStatus(TypedDict):
    configured: bool


class HuggingFaceKeyRequest(TypedDict):
    token: str


RunEvent = RunStarted | TurnStarted | ReplyDelta | StateCommitted | ActionReceipt | TurnCompleted | RunCompleted | RunCancelled | TurnFailed | RunFailed | RunCancelling


class ApiError(RuntimeError):
    """An HTTP failure, including RFC 9457 information when the server supplies it."""

    def __init__(self, response: httpx.Response):
        self.response = response
        self.status = response.status_code
        try:
            self.body = response.json()
        except ValueError:
            self.body = response.text
        self.problem: Problem | None = None
        self.request_id = response.headers.get("X-Request-ID")
        if isinstance(self.body, dict) and all(
            isinstance(self.body.get(key), kind)
            for key, kind in (
                ("type", str), ("title", str), ("status", int), ("detail", str),
                ("code", str), ("request_id", str), ("retryable", bool),
            )
        ):
            self.problem = self.body
            self.request_id = self.problem["request_id"]
        message = self.problem["detail"] if self.problem else f"HTTP {self.status}"
        super().__init__(message)


def _path_value(value: str) -> str:
    if value in ("", ".", ".."):
        raise ValueError("A path parameter must contain a resource identifier.")
    return quote(value, safe="")


def _scalar(value: Any) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


_OPERATIONS = {'liveness': ('GET', '/livez', False, (), (), 'application/json', None),
 'readiness': ('GET', '/readyz', False, (), (), 'application/json', None),
 'capabilities': ('GET',
                  '/v1/capabilities',
                  True,
                  (),
                  (),
                  'application/json',
                  None),
 'listWorkflows': ('GET',
                   '/v1/workflows',
                   True,
                   (),
                   (),
                   'application/json',
                   None),
 'createRun': ('POST',
               '/v1/runs',
               True,
               (('idempotency_key', 'Idempotency-Key', 'header'),),
               ('application/json',),
               'application/json',
               None),
 'getRun': ('GET',
            '/v1/runs/{run_id}',
            True,
            (('run_id', 'run_id', 'path'),),
            (),
            'application/json',
            None),
 'submitTurn': ('POST',
                '/v1/runs/{run_id}/turns',
                True,
                (('run_id', 'run_id', 'path'),
                 ('idempotency_key', 'Idempotency-Key', 'header')),
                ('application/json',),
                'application/json',
                None),
 'cancelRun': ('POST',
               '/v1/runs/{run_id}/cancel',
               True,
               (('run_id', 'run_id', 'path'),
                ('idempotency_key', 'Idempotency-Key', 'header')),
               ('application/json',),
               'application/json',
               None),
 'streamRun': ('GET',
               '/v1/runs/{run_id}/events',
               True,
               (('run_id', 'run_id', 'path'),
                ('last_event_id', 'Last-Event-ID', 'header')),
               (),
               'text/event-stream',
               'accept'),
 'chatCompletion': ('POST',
                    '/v1/chat/completions',
                    True,
                    (),
                    ('application/json',),
                    'application/json',
                    'body'),
 'getHuggingFaceKeyStatus': ('GET',
                             '/v1/control/huggingface',
                             True,
                             (),
                             (),
                             'application/json',
                             None),
 'saveHuggingFaceKey': ('PUT',
                        '/v1/control/huggingface',
                        True,
                        (),
                        ('application/json',),
                        'application/json',
                        None),
 'removeHuggingFaceKey': ('DELETE',
                          '/v1/control/huggingface',
                          True,
                          (),
                          (),
                          'application/json',
                          None),
 'listInstalledModels': ('GET',
                         '/v1/control/models',
                         True,
                         (),
                         (),
                         'application/json',
                         None),
 'setInstalledModelEnabled': ('PUT',
                              '/v1/control/models/{model_id}',
                              True,
                              (('model_id', 'model_id', 'path'),),
                              ('application/json',),
                              'application/json',
                              None),
 'getRuntimeTelemetry': ('GET',
                         '/v1/control/runtime',
                         True,
                         (('window_seconds', 'window_seconds', 'query'),
                          ('model', 'model', 'query'),
                          ('endpoint', 'endpoint', 'query')),
                         (),
                         'application/json',
                         None),
 'listOllamaCatalog': ('GET',
                       '/v1/control/ollama/catalog',
                       True,
                       (),
                       (),
                       'application/json',
                       None),
 'registerOllamaModel': ('POST',
                         '/v1/control/ollama/registrations',
                         True,
                         (),
                         ('application/json',),
                         'application/json',
                         None)}


class VoxrnAgentClient:
    """Synchronous client for the canonical VoxRN HTTP contract."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | Callable[[], str] | None = None,
        timeout: httpx.Timeout | float | None = 120.0,
        transport: httpx.BaseTransport | None = None,
    ):
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Use an HTTP(S) base URL without credentials, a query, or a fragment.")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._client = httpx.Client(
            timeout=timeout, transport=transport, follow_redirects=False
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> VoxrnAgentClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _request_args(
        self, operation: str, values: Mapping[str, Any], *, streaming: bool = False
    ) -> tuple[str, str, dict[str, Any]]:
        method, path, auth, parameters, media, accept, stream_selector = _OPERATIONS[operation]
        headers = {"Accept": "text/event-stream" if streaming else accept}
        query = {}
        for key, name, location in parameters:
            value = values.get(key)
            if value is None:
                continue
            if location == "path":
                path = path.replace("{" + name + "}", _path_value(value))
            elif location == "header":
                headers[name] = _scalar(value)
            else:
                query[name] = _scalar(value)
        if re.search(r"\{[^{}]+\}", path):
            raise ValueError("A required path parameter is missing.")
        if auth:
            token = self._token() if callable(self._token) else self._token
            if token is not None:
                headers["Authorization"] = f"Bearer {token}"

        kwargs: dict[str, Any] = {"headers": headers, "params": query}
        body = values.get("body")
        if body is not None and media == ("application/json",):
            if stream_selector == "body":
                if streaming:
                    body = {**body, "stream": True}
                elif body.get("stream", False):
                    raise ValueError("Use the streaming method for a streaming request.")
            kwargs["json"] = body
        elif body is not None and media == ("multipart/form-data",):
            data = {}
            files = {}
            for name, value in body.items():
                if isinstance(value, UploadPart):
                    files[name] = (value.filename, value.content, value.content_type)
                elif value is not None:
                    data[name] = _scalar(value)
            kwargs.update(data=data, files=files)
        elif body is not None:
            content_type = values.get("content_type")
            if content_type not in media:
                raise ValueError(f"Unsupported request Content-Type. Choose from {media}.")
            headers["Content-Type"] = content_type
            kwargs["content"] = body
        return method, self._base_url + path, kwargs

    def _request(self, operation: str, values: Mapping[str, Any]) -> httpx.Response:
        method, url, kwargs = self._request_args(operation, values)
        response = self._client.request(method, url, **kwargs)
        if not response.is_success:
            raise ApiError(response)
        return response

    @contextmanager
    def _stream(self, operation: str, values: Mapping[str, Any]) -> Iterator[httpx.Response]:
        method, url, kwargs = self._request_args(operation, values, streaming=True)
        with self._client.stream(method, url, **kwargs) as response:
            if not response.is_success:
                response.read()
                raise ApiError(response)
            yield response

    def liveness_raw(self) -> httpx.Response:
        return self._request("liveness", {})

    def liveness(self) -> Health:
        return self.liveness_raw().json()

    def readiness_raw(self) -> httpx.Response:
        return self._request("readiness", {})

    def readiness(self) -> Health:
        return self.readiness_raw().json()

    def capabilities_raw(self) -> httpx.Response:
        return self._request("capabilities", {})

    def capabilities(self) -> Capabilities:
        return self.capabilities_raw().json()

    def list_workflows_raw(self) -> httpx.Response:
        return self._request("listWorkflows", {})

    def list_workflows(self) -> Workflows:
        return self.list_workflows_raw().json()

    def create_run_raw(self, *, idempotency_key: str, body: RunRequest) -> httpx.Response:
        return self._request("createRun", {"idempotency_key": idempotency_key, "body": body})

    def create_run(self, *, idempotency_key: str, body: RunRequest) -> Run:
        return self.create_run_raw(idempotency_key=idempotency_key, body=body).json()

    def get_run_raw(self, *, run_id: str) -> httpx.Response:
        return self._request("getRun", {"run_id": run_id})

    def get_run(self, *, run_id: str) -> Run:
        return self.get_run_raw(run_id=run_id).json()

    def submit_turn_raw(self, *, run_id: str, idempotency_key: str, body: TurnRequest) -> httpx.Response:
        return self._request("submitTurn", {"run_id": run_id, "idempotency_key": idempotency_key, "body": body})

    def submit_turn(self, *, run_id: str, idempotency_key: str, body: TurnRequest) -> TurnAccepted:
        return self.submit_turn_raw(run_id=run_id, idempotency_key=idempotency_key, body=body).json()

    def cancel_run_raw(self, *, run_id: str, idempotency_key: str, body: CancelRequest) -> httpx.Response:
        return self._request("cancelRun", {"run_id": run_id, "idempotency_key": idempotency_key, "body": body})

    def cancel_run(self, *, run_id: str, idempotency_key: str, body: CancelRequest) -> Run:
        return self.cancel_run_raw(run_id=run_id, idempotency_key=idempotency_key, body=body).json()

    def stream_run(self, *, run_id: str, last_event_id: str | None = None) -> AbstractContextManager[httpx.Response]:
        return self._stream("streamRun", {"run_id": run_id, "last_event_id": last_event_id})

    def chat_completion_raw(self, *, body: ChatRequest) -> httpx.Response:
        return self._request("chatCompletion", {"body": body})

    def chat_completion(self, *, body: ChatRequest) -> ChatResponse:
        return self.chat_completion_raw(body=body).json()

    def chat_completion_stream(self, *, body: ChatRequest) -> AbstractContextManager[httpx.Response]:
        return self._stream("chatCompletion", {"body": body})

    def get_hugging_face_key_status_raw(self) -> httpx.Response:
        return self._request("getHuggingFaceKeyStatus", {})

    def get_hugging_face_key_status(self) -> HuggingFaceKeyStatus:
        return self.get_hugging_face_key_status_raw().json()

    def save_hugging_face_key_raw(self, *, body: HuggingFaceKeyRequest) -> httpx.Response:
        return self._request("saveHuggingFaceKey", {"body": body})

    def save_hugging_face_key(self, *, body: HuggingFaceKeyRequest) -> HuggingFaceKeyStatus:
        return self.save_hugging_face_key_raw(body=body).json()

    def remove_hugging_face_key_raw(self) -> httpx.Response:
        return self._request("removeHuggingFaceKey", {})

    def remove_hugging_face_key(self) -> HuggingFaceKeyStatus:
        return self.remove_hugging_face_key_raw().json()

    def list_installed_models_raw(self) -> httpx.Response:
        return self._request("listInstalledModels", {})

    def list_installed_models(self) -> InstalledModels:
        return self.list_installed_models_raw().json()

    def set_installed_model_enabled_raw(self, *, model_id: str, body: ModelEnableRequest) -> httpx.Response:
        return self._request("setInstalledModelEnabled", {"model_id": model_id, "body": body})

    def set_installed_model_enabled(self, *, model_id: str, body: ModelEnableRequest) -> InstalledModel:
        return self.set_installed_model_enabled_raw(model_id=model_id, body=body).json()

    def get_runtime_telemetry_raw(self, *, window_seconds: Literal[60, 300, 900] | None = None, model: str | None = None, endpoint: str | None = None) -> httpx.Response:
        return self._request("getRuntimeTelemetry", {"window_seconds": window_seconds, "model": model, "endpoint": endpoint})

    def get_runtime_telemetry(self, *, window_seconds: Literal[60, 300, 900] | None = None, model: str | None = None, endpoint: str | None = None) -> RuntimeTelemetry:
        return self.get_runtime_telemetry_raw(window_seconds=window_seconds, model=model, endpoint=endpoint).json()

    def list_ollama_catalog_raw(self) -> httpx.Response:
        return self._request("listOllamaCatalog", {})

    def list_ollama_catalog(self) -> OllamaCatalog:
        return self.list_ollama_catalog_raw().json()

    def register_ollama_model_raw(self, *, body: OllamaRegistrationRequest) -> httpx.Response:
        return self._request("registerOllamaModel", {"body": body})

    def register_ollama_model(self, *, body: OllamaRegistrationRequest) -> InstalledModel:
        return self.register_ollama_model_raw(body=body).json()
