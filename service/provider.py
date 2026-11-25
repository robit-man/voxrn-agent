"""Native Ollama transport for stateless text and source-bound structured requests."""

import os
from dataclasses import dataclass

import httpx

from .ollama_protocol import chat_packet, chat_payload
from .wire import Problem, strict_json


@dataclass(frozen=True)
class OllamaProfile:
    model: str
    tag: str
    digest: str
    engine_revision: str
    url: str
    context_tokens: int = 8192
    thinking: bool | None = None
    credential_env: str | None = None
    structured_outputs: bool = True
    enabled: bool = True
    managed: bool = False

    @classmethod
    def from_legacy_environment(cls):
        thinking = os.environ.get("VOXRN_AGENT_THINK")
        if thinking not in (None, "true", "false"):
            raise ValueError("VOXRN_AGENT_THINK must be true or false.")
        return cls(
            model="qwen-local",
            tag=os.environ["VOXRN_AGENT_MODEL_TAG"],
            digest=os.environ["VOXRN_AGENT_MODEL_DIGEST"],
            engine_revision=os.environ["VOXRN_AGENT_ENGINE_REVISION"],
            url=os.environ["VOXRN_AGENT_OLLAMA_URL"],
            context_tokens=int(os.environ.get("VOXRN_AGENT_CONTEXT_TOKENS", "8192")),
            thinking=None if thinking is None else thinking == "true",
            credential_env="VOXRN_AGENT_PROVIDER_TOKEN",
        )


class Ollama:
    def __init__(self, profile=None):
        self.profile = profile or OllamaProfile.from_legacy_environment()
        self.url = self.profile.url.rstrip("/")
        self.identity = {
            "model": self.profile.model,
            "model_revision": self.profile.digest,
            "engine": "ollama",
            "engine_revision": self.profile.engine_revision,
        }
        self.runner_key = (self.identity["engine"], self.url, self.profile.tag)
        self.managed = self.profile.managed
        self.default_enabled = self.profile.enabled
        self.features = {
            "chat": True,
            "chat_stream": True,
            "structured_outputs": self.profile.structured_outputs,
        }
        self.client = None
        self._may_be_resident = False

    async def start(self):
        credential = (
            os.environ.get(self.profile.credential_env)
            if self.profile.credential_env
            else None
        )
        headers = {"Authorization": f"Bearer {credential}"} if credential else {}
        self.client = httpx.AsyncClient(
            base_url=self.url,
            headers=headers,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(90, connect=5),
        )

    async def close(self):
        if self.client is None:
            return
        # Only an installer-owned runner is safe to evict. Keep the client for
        # a subsequent cleanup attempt if the provider cannot verify release.
        if self.managed and self._may_be_resident:
            try:
                await self.require_ready()
                response = await self.client.post(
                    "/api/generate",
                    json={"model": self.profile.tag, "keep_alive": 0, "stream": False},
                    timeout=15,
                )
                response.raise_for_status()
                receipt = response.json()
                if (
                    receipt.get("done") is not True
                    or receipt.get("done_reason") != "unload"
                    or receipt.get("model") != self.profile.tag
                ):
                    raise ValueError("Ollama did not acknowledge unloading the model.")
            except (Problem, httpx.HTTPError, ValueError, AttributeError) as exc:
                raise Problem(
                    503,
                    "provider_release_unverified",
                    "The managed provider could not verify that it released the model.",
                    retryable=True,
                ) from exc
        self._may_be_resident = False
        client, self.client = self.client, None
        await client.aclose()

    async def ready(self):
        client = self.client
        if client is None:
            return False
        try:
            version = await client.get("/api/version", timeout=3)
            tags = await client.get("/api/tags", timeout=3)
            version.raise_for_status()
            tags.raise_for_status()
            return version.json()["version"] == self.profile.engine_revision and any(
                item["name"] == self.profile.tag
                and item["digest"] == self.profile.digest
                for item in tags.json()["models"]
            )
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError):
            return False

    async def require_ready(self):
        if not await self.ready():
            raise Problem(
                503,
                "provider_not_ready",
                "The configured provider or model revision is unavailable.",
                retryable=True,
            )

    async def complete(self, messages, tokens, schema=None):
        payload = chat_payload(
            self.profile, messages, tokens, stream=False, schema=schema
        )
        await self.require_ready()
        self._may_be_resident = True
        try:
            response = await self.client.post("/api/chat", json=payload)
            response.raise_for_status()
            packet = response.json()
        except httpx.TimeoutException as exc:
            raise Problem(
                504,
                "provider_deadline",
                "Provider inference exceeded its deadline.",
                retryable=True,
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise Problem(
                502,
                "provider_transport",
                "The inference provider request failed.",
                retryable=True,
            ) from exc
        content, reason = chat_packet(packet)
        if not reason:
            raise Problem(
                502,
                "provider_protocol",
                "The provider did not finalize its buffered reply.",
            )
        return content, reason

    async def stream(self, messages, tokens):
        payload = chat_payload(self.profile, messages, tokens, stream=True)
        await self.require_ready()
        self._may_be_resident = True
        try:
            async with self.client.stream(
                "POST", "/api/chat", json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        chunk, reason = chat_packet(strict_json(line))
                    except Problem as exc:
                        raise Problem(
                            502,
                            "provider_protocol",
                            "The provider returned an invalid stream packet.",
                        ) from exc
                    if chunk:
                        yield chunk, None
                    if reason:
                        yield "", reason
                        return
        except httpx.TimeoutException as exc:
            raise Problem(
                504,
                "provider_deadline",
                "Provider inference exceeded its deadline.",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise Problem(
                502,
                "provider_transport",
                "The inference provider stream failed.",
                retryable=True,
            ) from exc
        raise Problem(
            502,
            "provider_incomplete",
            "The provider stream closed without a terminal packet.",
        )
