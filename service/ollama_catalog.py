"""Expose an operator-selected Ollama inventory and persist immutable chat imports."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import httpx

from . import provider as transport
from .managed_library import ManagedAdapter
from .wire import Problem, canonical_json, validate


class ImportedOllama(transport.Ollama):
    async def start(self):
        await super().start()
        await self.require_ready()


class OllamaCatalog:
    def __init__(self, providers, path):
        self.path = path
        self.configured = {}
        for key, adapter in providers.items():
            candidate = (
                adapter.adapter if isinstance(adapter, ManagedAdapter) else adapter
            )
            if isinstance(candidate, transport.Ollama):
                self.configured[key] = candidate
        self.url = None
        self.credential_env = None
        self.context_tokens = 8192
        selected = os.environ.get("VOXRN_AGENT_OLLAMA_CATALOG_MODEL")
        external = os.environ.get("VOXRN_AGENT_OLLAMA_CATALOG_URL")
        if selected and external:
            raise ValueError(
                "Choose either a configured catalog model or an external catalog URL."
            )
        if external:
            self.url = transport.validated_ollama_url(external)
            self.credential_env = "VOXRN_AGENT_OLLAMA_CATALOG_TOKEN"
        elif selected:
            if selected not in self.configured:
                raise ValueError(
                    "The selected catalog model is not a configured Ollama profile."
                )
            self._select(self.configured[selected])
        elif self.configured:
            backends = {
                (adapter.url, adapter.profile.credential_env)
                for adapter in self.configured.values()
            }
            if len(backends) == 1:
                self._select(next(iter(self.configured.values())))
        self.backend = self._key([self.url, self.credential_env])
        self.records = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(data, dict)
                or data.get("version") != 1
                or not isinstance(data.get("backends"), dict)
            ):
                raise RuntimeError("Unsupported Ollama registrations format.")
            self.records = data["backends"]

    def _select(self, adapter):
        self.url = adapter.url
        self.credential_env = adapter.profile.credential_env
        self.context_tokens = min(self.context_tokens, adapter.profile.context_tokens)

    @staticmethod
    def _key(value):
        return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

    def _alias(self, tag, digest, revision):
        slug = re.sub("[^a-z0-9]+", "-", tag.lower()).strip("-")[:90]
        return (
            "ollama-"
            + slug
            + "-"
            + self._key([self.backend, tag, digest, revision])[:16]
        )

    def _imported(self, item):
        alias = self._alias(item["tag"], item["digest"], item["engine_revision"])
        return ImportedOllama(
            transport.OllamaProfile(
                model=alias,
                tag=item["tag"],
                digest=item["digest"],
                engine_revision=item["engine_revision"],
                url=self.url,
                credential_env=self.credential_env,
                context_tokens=self.context_tokens,
                structured_outputs=False,
                enabled=False,
            )
        )

    def _configured_tag(self, tag):
        return {
            alias: candidate
            for alias, candidate in self.configured.items()
            if candidate.url == self.url and candidate.profile.tag == tag
        }

    def restore(self, providers):
        if self.url is None:
            return {}
        restored = {}
        for item in self.records.get(self.backend, ()):
            validate(item, "OllamaRegistrationPin")
            if self._configured_tag(item["tag"]):
                continue
            adapter = self._imported(item)
            alias = adapter.identity["model"]
            if alias not in providers:
                restored[alias] = adapter
        return restored

    async def _snapshot(self):
        if self.url is None:
            raise Problem(
                503,
                "ollama_catalog_unconfigured",
                "Configure one Ollama backend for catalog discovery.",
            )
        try:
            async with transport.ollama_client(self.url, self.credential_env) as client:
                version = (
                    (await client.get("/api/version", timeout=3))
                    .raise_for_status()
                    .json()
                )
                inventory = (
                    (await client.get("/api/tags", timeout=3)).raise_for_status().json()
                )
        except httpx.HTTPError as exc:
            raise Problem(
                503,
                "ollama_catalog_unavailable",
                "The configured Ollama catalog could not be reached.",
                retryable=True,
            ) from exc
        except ValueError as exc:
            raise self._invalid_catalog() from exc
        try:
            provider = {
                "url": self.url,
                "engine": "ollama",
                "engine_revision": version["version"],
            }
            models = {}
            for item in inventory["models"]:
                entry = {
                    "tag": item["name"],
                    "digest": item["digest"],
                    "size_bytes": item["size"],
                }
                if entry["tag"] in models and models[entry["tag"]] != entry:
                    raise ValueError("Ambiguous tag in the provider catalog.")
                models[entry["tag"]] = entry
            validate(
                {"provider": provider, "models": list(models.values())},
                "OllamaCatalog",
                status=502,
            )
        except (Problem, KeyError, TypeError, ValueError) as exc:
            raise self._invalid_catalog() from exc
        return provider, models

    @staticmethod
    def _invalid_catalog():
        return Problem(
            502,
            "ollama_catalog_invalid",
            "Ollama returned an invalid or ambiguous catalog.",
        )

    async def _require_completion(self, tag):
        try:
            async with transport.ollama_client(self.url, self.credential_env) as client:
                response = await client.post(
                    "/api/show", json={"model": tag}, timeout=5
                )
                if response.status_code == 404:
                    raise Problem(
                        409,
                        "ollama_pin_changed",
                        "The tag disappeared. Refresh the Ollama catalog.",
                    )
                metadata = response.raise_for_status().json()
        except httpx.HTTPError as exc:
            raise Problem(
                503,
                "ollama_catalog_unavailable",
                "The configured Ollama model metadata could not be reached.",
                retryable=True,
            ) from exc
        except ValueError as exc:
            raise self._invalid_catalog() from exc
        if not isinstance(metadata, dict):
            raise self._invalid_catalog()
        if "capabilities" not in metadata:
            return
        capabilities = metadata["capabilities"]
        if not isinstance(capabilities, list) or not all(
            isinstance(item, str) for item in capabilities
        ):
            raise self._invalid_catalog()
        if "completion" not in capabilities:
            raise Problem(
                409,
                "ollama_chat_unsupported",
                "The Ollama model does not advertise text completion.",
            )

    def _registered(self, item, revision, providers):
        tag = item["tag"]
        candidates = self._configured_tag(tag)
        alias = self._alias(tag, item["digest"], revision)
        if isinstance(providers.get(alias), ImportedOllama):
            candidates[alias] = providers[alias]
        return next(
            (
                key
                for key, adapter in candidates.items()
                if adapter.identity["model_revision"] == item["digest"]
                and adapter.identity["engine_revision"] == revision
            ),
            None,
        )

    async def list(self, providers):
        provider, models = await self._snapshot()
        for item in models.values():
            alias = self._registered(item, provider["engine_revision"], providers)
            if alias is not None:
                item["registered_model"] = alias
        return {"provider": provider, "models": [models[tag] for tag in sorted(models)]}

    def _persist(self, item):
        current = self.records.get(self.backend, [])
        records = {**self.records, self.backend: [*current, item]}
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, delete=False
            ) as handle:
                temporary = Path(handle.name)
                try:
                    json.dump({"version": 1, "backends": records}, handle)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                    os.replace(temporary, self.path)
                finally:
                    temporary.unlink(missing_ok=True)
        except OSError as exc:
            raise Problem(
                503,
                "ollama_registration_write_failed",
                "The model registration could not be saved.",
                retryable=True,
            ) from exc
        self.records = records

    async def register(self, requested, providers, control):
        provider, inventory = await self._snapshot()
        tag, digest = requested["tag"], requested["digest"]
        if tag not in inventory or inventory[tag]["digest"] != digest:
            raise Problem(
                409,
                "ollama_pin_changed",
                "The tag or digest changed. Refresh the Ollama catalog.",
            )
        revision = provider["engine_revision"]
        alias = self._registered(requested, revision, providers)
        if alias is not None:
            return control.describe(alias), 200
        if self._configured_tag(tag):
            raise Problem(
                409,
                "ollama_tag_already_configured",
                "A configured profile already owns this Ollama tag.",
            )
        await self._require_completion(tag)
        alias = self._registered(requested, revision, providers)
        if alias is not None:
            return control.describe(alias), 200
        pin = {**requested, "engine_revision": revision}
        adapter = self._imported(pin)
        registered = control.register_disabled(adapter, lambda: self._persist(pin))
        providers[adapter.identity["model"]] = adapter
        return registered, 201
