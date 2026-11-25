"""Translate between VoxRN chat requests and Ollama's native wire protocol."""

from .wire import Problem, canonical_json


def chat_payload(profile, messages, tokens, *, stream, schema=None):
    # Keep the complete physical request. The admission bound leaves room for
    # Ollama's chat template and rejects oversized inputs before transport.
    byte_count = len(canonical_json(messages).encode("ascii"))
    if schema is not None:
        byte_count += len(canonical_json(schema).encode("ascii"))
    if byte_count + tokens + 512 > profile.context_tokens:
        raise Problem(
            422,
            "context_too_large",
            "The complete request exceeds the configured context bound.",
        )
    if schema is not None and not profile.structured_outputs:
        raise Problem(
            422,
            "model_capability_unavailable",
            "This model cannot serve structured workflows.",
        )
    payload = {
        "model": profile.tag,
        "messages": messages,
        "stream": stream,
        "options": {"num_ctx": profile.context_tokens, "num_predict": tokens},
    }
    if profile.thinking is not None:
        payload["think"] = profile.thinking
    if schema is not None:
        payload["format"] = schema
    return payload


def chat_packet(packet):
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
