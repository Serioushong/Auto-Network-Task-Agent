"""T027 — structlog processor that redacts user-supplied payload before write.

Strategy (FR-020): allowlist + value-length threshold.

- Keys in `SAFE_KEYS` pass through unchanged (system identifiers, enums,
  timestamps, hashes that are already one-way).
- Any other key whose value is a large string or nested object is replaced
  with a marker like `<redacted:123-bytes:sha256-<32hex>>` so that:
    * The original content is never written to the audit log (FR-020 / FR-023).
    * A hash sidecar lets operators correlate (e.g. "the 3 failures all had
      the same redacted payload").
    * Log size stays bounded even on pathological inputs.
"""

from __future__ import annotations

import json
from collections.abc import MutableMapping
from typing import Any

from .hasher import sha256_trunc16

EventDict = MutableMapping[str, Any]

SAFE_KEYS: frozenset[str] = frozenset(
    {
        # structlog + stdlib builtins
        "event",
        "timestamp",
        "level",
        "logger",
        # AuditEvent top-level identifiers
        "auditId",
        "traceId",
        "taskId",
        "parentTaskId",
        "eventId",
        "userId",
        "workerId",
        "pid",
        "actor",
        "capability",
        "eventType",
        "outcome",
        "failureReason",
        "failureDim",
        "riskLevel",
        "sourceChannel",
        "kind",
        "decision",
        "deliveryAttempt",
        "traceOutcome",
        "idempotent_replay",
        # Already-hashed fields
        "input_hash",
        "output_hash",
        "resultHash",
        "commandDigest",
    }
)
"""Keys whose value is NOT user-derived, or is already hashed. Passed through."""

DEFAULT_VALUE_THRESHOLD_BYTES: int = 256
"""Strings or nested objects over this many UTF-8 bytes get redacted."""


def _redact_value(value: Any, threshold: int) -> Any:
    if isinstance(value, str):
        as_bytes = value.encode("utf-8")
        if len(as_bytes) > threshold:
            return f"<redacted:{len(as_bytes)}-bytes:sha256-{sha256_trunc16(as_bytes)}>"
        return value
    if isinstance(value, (dict, list)):
        canonical = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
            ensure_ascii=False,
        )
        encoded = canonical.encode("utf-8")
        if len(encoded) > threshold:
            return f"<redacted:obj:{len(encoded)}-bytes:sha256-{sha256_trunc16(encoded)}>"
        return value
    return value


def redact(
    event_dict: EventDict,
    *,
    safe_keys: frozenset[str] = SAFE_KEYS,
    threshold: int = DEFAULT_VALUE_THRESHOLD_BYTES,
) -> EventDict:
    """Mutate `event_dict` in place, redacting non-allowlist large values."""
    for key in list(event_dict.keys()):
        if key in safe_keys:
            continue
        event_dict[key] = _redact_value(event_dict[key], threshold)
    return event_dict


def make_structlog_processor(
    *,
    safe_keys: frozenset[str] = SAFE_KEYS,
    threshold: int = DEFAULT_VALUE_THRESHOLD_BYTES,
) -> Any:
    """Factory returning a callable matching structlog's processor signature."""

    def _processor(_logger: Any, _method_name: str, event_dict: EventDict) -> EventDict:
        return redact(event_dict, safe_keys=safe_keys, threshold=threshold)

    return _processor
