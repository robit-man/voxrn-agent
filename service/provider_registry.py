"""Read locally installed model profiles and dispatch them to provider adapters."""

import os
import re
from pathlib import Path
from .provider import Ollama, OllamaProfile, validated_ollama_url
from .wire import strict_json


def ollama_profile(record):
    fields = set(record)
    required = {"model", "tag", "digest", "engine", "engine_revision", "url"}
    optional = {
        "context_tokens",
        "think",
        "credential_env",
        "structured_outputs",
        "enabled",
        "managed",
    }
    if not required <= fields or fields - required - optional:
        raise ValueError("The Ollama profile has missing or unsupported fields.")
    if not all(
        isinstance(record[field], str) and record[field].strip() for field in required
    ):
        raise ValueError(
            "The Ollama model identity and endpoint must be nonempty strings."
        )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", record["model"]):
        raise ValueError("The public model ID is invalid.")
    if not re.fullmatch(r"[a-f0-9]{64}", record["digest"]):
        raise ValueError("The model digest must be a lowercase SHA-256.")
    context = record.get("context_tokens", 8192)
    if type(context) is not int or context < 1024:
        raise ValueError("The context token limit must be an integer of at least 1024.")
    for key in ("think", "structured_outputs", "enabled", "managed"):
        if key in record and type(record[key]) is not bool:
            raise ValueError(f"The {key} profile setting must be boolean.")
    credential_env = record.get("credential_env")
    if credential_env is not None and (
        not isinstance(credential_env, str)
        or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", credential_env)
    ):
        raise ValueError("The provider credential variable name is invalid.")
    return OllamaProfile(
        model=record["model"],
        tag=record["tag"],
        digest=record["digest"],
        engine_revision=record["engine_revision"],
        url=validated_ollama_url(record["url"]),
        context_tokens=context,
        thinking=record.get("think"),
        credential_env=credential_env,
        structured_outputs=record.get("structured_outputs", True),
        enabled=record.get("enabled", True),
        managed=record.get("managed", False),
    )


PROVIDERS = {"ollama": lambda record: Ollama(ollama_profile(record))}


def load_providers():
    filename = os.environ.get("VOXRN_AGENT_MODELS_FILE")
    if not filename:
        has_legacy_pin = any(
            os.environ.get(name)
            for name in (
                "VOXRN_AGENT_MODEL_TAG",
                "VOXRN_AGENT_MODEL_DIGEST",
                "VOXRN_AGENT_ENGINE_REVISION",
            )
        )
        if os.environ.get("VOXRN_AGENT_OLLAMA_CATALOG_URL") and not has_legacy_pin:
            return {}
        provider = Ollama()
        return {"qwen-local": provider}
    config = strict_json(Path(filename).read_bytes())
    if (
        not isinstance(config, dict)
        or set(config) != {"models"}
        or not isinstance(config["models"], list)
    ):
        raise ValueError("The provider registry must contain a models list.")
    providers = {}
    upstreams = {}
    for record in config["models"]:
        if not isinstance(record, dict) or not isinstance(record.get("engine"), str):
            raise ValueError(
                "Each model profile must name a supported provider engine."
            )
        factory = PROVIDERS.get(record["engine"])
        if factory is None:
            raise ValueError("The model profile names an unsupported provider engine.")
        provider = factory(record)
        alias = provider.identity["model"]
        if alias in providers:
            raise ValueError("Public model IDs must be unique.")
        upstream = provider.runner_key
        if upstream in upstreams and (provider.managed or upstreams[upstream]):
            raise ValueError(
                "An installer-owned runner tag must belong to a single profile."
            )
        upstreams[upstream] = provider.managed
        providers[alias] = provider
    return providers
