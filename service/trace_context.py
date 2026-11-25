"""Carry an interoperable W3C traceparent across the HTTP boundary."""

import re
import secrets
from contextvars import ContextVar

CURRENT_TRACE = ContextVar("voxrn_traceparent", default=None)
VERSION_ZERO = re.compile(rb"00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})")
ZERO_TRACE = b"0" * 32
ZERO_SPAN = b"0" * 16


def start_span(headers):
    candidates = [value for name, value in headers if name == b"traceparent"]
    match = VERSION_ZERO.fullmatch(candidates[0]) if len(candidates) == 1 else None
    if match and match[1] != ZERO_TRACE and match[2] != ZERO_SPAN:
        trace_id = match[1].decode("ascii")
        sampled = int(match[3], 16) & 1
    else:
        trace_id = secrets.token_hex(16)
        sampled = 0
    return f"00-{trace_id}-{secrets.token_hex(8)}-{sampled:02x}"


def provider_headers():
    parent = CURRENT_TRACE.get()
    return {"traceparent": parent} if parent else {}
