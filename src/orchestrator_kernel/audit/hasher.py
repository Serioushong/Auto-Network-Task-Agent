"""T027 — SHA-256 truncated to first 16 bytes (32 lowercase hex chars).

Single source for every hash that crosses a contract boundary: `input_hash` /
`output_hash` in AuditEvent, `resultHash` in Task and in the worker protocol
ResultFrame. The pattern `^[0-9a-f]{32}$` in the JSON schemas is enforced on
this exact output shape — do not change the truncation length.

32 hex chars = 128 bits of collision resistance. For audit correlation we need
lookup uniqueness, not cryptographic security; 128 bits over a session of
millions of events has negligible collision probability.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

_HASH_HEX_LENGTH = 32


def sha256_trunc16(data: bytes | str) -> str:
    """Hash `data` with SHA-256, return first 16 bytes as 32 lowercase hex chars."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:_HASH_HEX_LENGTH]


def hash_canonical_json(obj: Any) -> str:
    """Canonical-JSON hash for dict/list payloads crossing boundaries.

    Canonicalisation rules: keys sorted, no whitespace, `default=str` to keep
    datetime / pydantic models stringify-able. The goal is determinism, not
    portability with other canonical-JSON specs — as long as the kernel always
    uses this same helper, input_hash / output_hash are comparable.
    """
    canonical = json.dumps(
        obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False
    ).encode("utf-8")
    return sha256_trunc16(canonical)
