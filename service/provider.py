"""Native Ollama transport for stateless text and source-bound structured requests."""

import os

import httpx

from .wire import Problem, canonical_json, strict_json


class Ollama:
    def __init__(self):
        self.url = os.environ["VOXRN_AGENT_OLLAMA_URL"].rstrip("/")
        self.tag = os.environ["VOXRN_AGENT_MODEL_TAG"]
        self.context_tokens = int(os.environ.get("VOXRN_AGENT_CONTEXT_TOKENS", "8192"))
        self.identity = {
            "model": "qwen-local",
            "model_revision": os.environ["VOXRN_AGENT_MODEL_DIGEST"],
            "engine": "ollama",
            "engine_revision": os.environ["VOXRN_AGENT_ENGINE_REVISION"],
        }
        self.client = None

    async def start(self):
        credential = os.environ.get("VOXRN_AGENT_PROVIDER_TOKEN")
        headers = {"Authorization": f"Bearer {credential}"} if credential else {}
        self.client = httpx.AsyncClient(
            base_url=self.url,
            headers=headers,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(90, connect=5),
        )

    async def close(self):
        if self.client:
            await self.client.aclose()

    async def ready(self):
        try:
            version = await self.client.get("/api/version", timeout=3)
            tags = await self.client.get("/api/tags", timeout=3)
            version.raise_for_status()
            tags.raise_for_status()
            return version.json()["version"] == self.identity[
                "engine_revision"
            ] and any(
                item["name"] == self.tag
                and item["digest"] == self.identity["model_revision"]
                for item in tags.json()["models"]
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return False

    def request(self, messages, tokens, stream, schema=None):
        # Bound input conservatively to leave room for tokenization and chat framing.
        # Providers can reject smaller requests based on their real context window.
        byte_count = len(canonical_json(messages).encode("ascii"))
        if schema:
            byte_count += len(canonical_json(schema).encode("ascii"))
        if byte_count + tokens + 512 > self.context_tokens:
            raise Problem(
                422,
                "context_too_large",
                "The complete request exceeds the configured context bound.",
            )
        payload = {
            "model": self.tag,
            "messages": messages,
            "stream": stream,
            "options": {"num_ctx": self.context_tokens, "num_predict": tokens},
        }
        if schema is not None:
            payload["format"] = schema
        return payload

    @staticmethod
    def parse(packet):
        try:
            if (
                not isinstance(packet, dict)
                or "error" in packet
                or type(packet["done"]) is not bool
            ):
                raise ValueError
            content = packet["message"]["content"]
            if not isinstance(content, str):
                raise ValueError
            reason = None
            if packet["done"]:
                reason = packet.get("done_reason", "stop")
                if reason not in ("stop", "length"):
                    raise ValueError
            return content, reason
        except (ValueError, TypeError, KeyError) as exc:
            raise Problem(
                502,
                "provider_protocol",
                "The provider returned an invalid chat packet.",
            ) from exc

    async def complete(self, messages, tokens, schema=None):
        if not await self.ready():
            raise Problem(
                503,
                "provider_not_ready",
                "The configured provider or model revision is unavailable.",
                retryable=True,
            )
        payload = self.request(messages, tokens, False, schema)
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
        content, reason = self.parse(packet)
        if not reason:
            raise Problem(
                502,
                "provider_protocol",
                "The provider did not finalize its buffered reply.",
            )
        return content, reason

    async def stream(self, messages, tokens):
        if not await self.ready():
            raise Problem(
                503,
                "provider_not_ready",
                "The configured provider or model revision is unavailable.",
                retryable=True,
            )
        payload = self.request(messages, tokens, True)
        try:
            async with self.client.stream(
                "POST", "/api/chat", json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        chunk, reason = self.parse(strict_json(line))
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
