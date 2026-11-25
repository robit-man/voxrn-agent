"""Admission of immutable local workflow artifacts and the governed intake ABI."""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import rfc8785
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .wire import CONTRACTS, Problem, strict_json, validate

ENTRYPOINT = "record_intake"
BINDING_SET = {
    "record_request": {"binding_id": "native_request_log", "effect": "record"}
}
WORKFLOW_SCHEMA = json.loads((CONTRACTS / "workflow-bundle.schema.json").read_text())
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def decision_branch(action, day):
    return {
        "type": "object",
        "properties": {
            "action": {"const": action},
            "day": day,
            "reply": {"type": "string", "minLength": 1},
        },
        "required": ["action", "day", "reply"],
        "additionalProperties": False,
    }


CLARIFICATION = decision_branch("clarify", {"const": ""})
DECISION = {
    "oneOf": [
        CLARIFICATION,
        decision_branch("record", {"enum": list(DAYS)}),
    ],
}


def authorized_weekday(text):
    """The intake ABI permits a record with a single explicit weekday and no negation."""
    if re.search(
        r"\b(?:no|never|not|cancel|don't|cannot|can't|won't)\b",
        text,
        re.IGNORECASE,
    ):
        return None
    days = [day for day in DAYS if re.search(rf"\b{day}\b", text, re.IGNORECASE)]
    return days[0] if len(days) == 1 else None


def digest(value):
    return hashlib.sha256(value).hexdigest()


def checked_schema(schema):
    if (
        schema.get("$schema", "https://json-schema.org/draft/2020-12/schema")
        != "https://json-schema.org/draft/2020-12/schema"
    ):
        raise RuntimeError("Workflow schemas must use JSON Schema 2020-12.")

    def reject_refs(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ("$ref", "$dynamicRef"):
                    raise RuntimeError(
                        "The local workflow ABI accepts self-contained, reference-free schemas."
                    )
                reject_refs(child)
        elif isinstance(value, list):
            for child in value:
                reject_refs(child)

    reject_refs(schema)
    Draft202012Validator.check_schema(schema)


@dataclass
class Workflow:
    manifest: dict
    source: str
    instructions: str

    @property
    def identity(self):
        names = (
            "id",
            "revision",
            "source_sha256",
            "executable_sha256",
            "binding_set_sha256",
            "identity",
        )
        return {name: self.manifest[name] for name in names}

    async def propose(self, provider, history, state, input_text, tokens):
        context = {
            "source": self.source,
            "committed_state": state,
            "operations": self.manifest["operations"],
            "source_sha256": self.manifest["source_sha256"],
        }
        # Preserve the exact participant message after the saved source, prior history, and typed bindings.
        messages = [
            {"role": "system", "content": self.instructions},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            *history,
            {"role": "user", "content": input_text},
        ]
        authorized_day = authorized_weekday(input_text)
        decision_schema = DECISION if authorized_day else CLARIFICATION
        output, reason = await provider.complete(messages, tokens, decision_schema)
        if reason != "stop":
            raise Problem(
                502,
                "provider_incomplete",
                "The provider did not finish the structured decision.",
            )
        try:
            decision = validate(strict_json(output), DECISION, status=502)
        except Problem as exc:
            raise Problem(
                502,
                "invalid_workflow_decision",
                "The provider decision did not match the workflow schema.",
            ) from exc
        if decision["action"] == "clarify":
            return None
        # Also enforce caller authority if the provider ignores its offered schema.
        day = decision["day"]
        if day != authorized_day:
            raise Problem(
                422,
                "source_evidence_missing",
                "A record requires explicit, unambiguous weekday evidence.",
            )
        operation = self.manifest["operations"][0]
        proposed = {"day": day}
        validate(proposed, operation["input_schema"], status=502)
        validate(proposed, self.manifest["state_schema"], status=502)
        return proposed


def load_bundle(path, identity=None, *, model_identities=None):
    path = Path(path)
    manifest = validate(
        strict_json((path / "manifest.json").read_bytes()), WORKFLOW_SCHEMA
    )
    if model_identities is not None:
        identity = model_identities.get(manifest["identity"]["model"])
    source = (path / "source.txt").read_bytes()
    executable = (path / "instructions.txt").read_bytes()
    if (
        manifest["abi"] != "workflow-v1"
        or manifest["entrypoint"] != ENTRYPOINT
        or manifest["identity"] != identity
        or manifest["source_sha256"] != digest(source)
        or manifest["executable_sha256"] != digest(executable)
        or manifest["binding_set_sha256"] != digest(rfc8785.dumps(BINDING_SET))
    ):
        raise RuntimeError(
            "Workflow ABI, artifact digest or model identity did not match."
        )
    operations = manifest["operations"]
    if len(operations) != 1 or operations[0]["id"] not in BINDING_SET:
        raise RuntimeError("The workflow contains an unbound operation.")
    operation = operations[0]
    if {name: operation[name] for name in ("binding_id", "effect")} != BINDING_SET[
        operation["id"]
    ]:
        raise RuntimeError(
            "The requested binding or effect is outside the configured registry."
        )
    try:
        for schema in (
            manifest["state_schema"],
            operation["input_schema"],
            operation["output_schema"],
        ):
            checked_schema(schema)
        validate(manifest["initial_state"], manifest["state_schema"])
    except (SchemaError, Problem) as exc:
        raise RuntimeError("A workflow schema or initial state is invalid.") from exc
    return Workflow(manifest, source.decode("utf-8"), executable.decode("utf-8"))
