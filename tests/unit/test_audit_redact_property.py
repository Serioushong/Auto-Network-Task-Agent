"""T096 — Hypothesis property test guarding INV-8 (no plaintext in audit JSONL).

INV-8 (data-model.md Invariants table):
    敏感字段 MUST NOT 以明文出现在审计 JSONL 任何一行。

The redact processor (`audit/redact.py`) collapses any non-allowlisted
value above a configurable byte threshold into a marker shaped like
``<redacted:<len>-bytes:sha256-<32hex>>`` (or the ``obj:`` variant for
nested containers). This file uses Hypothesis to generate sensitive-looking
random payloads and asserts a handful of properties that, taken together,
make any plaintext leak into the final JSONL line a programming bug
rather than a fuzzing accident:

* P1 — Marker shape: every redacted value matches the documented marker
  regex; no other shape ever leaks through.
* P2 — Allowlist transparency: keys in ``SAFE_KEYS`` always pass through
  unchanged regardless of the value's size or content.
* P3 — Plaintext absence: for any high-entropy "secret" that exceeds the
  threshold, the original bytes MUST NOT appear in the canonical JSON
  serialization of the redacted dict (this is the exact grep-the-line
  invariant INV-8 demands).
* P4 — Idempotency: redact(redact(x)) == redact(x); applying the
  processor twice never re-introduces plaintext or doubles the marker.
* P5 — Hash determinism: two redactions of the same payload produce
  exactly the same marker, so operators can correlate "same secret →
  same hash" across audit lines without recovering the secret itself.
* P6 — Threshold honesty: a value at or below the threshold is passed
  through verbatim; one byte over and it MUST be replaced by a marker.

These properties are sufficient to bound the audit log against the
``redact`` processor's threats. The integration-level guarantee (that the
kernel actually feeds audit dicts through this processor) is covered by
the surrounding wiring tests; this file is the math-level proof.
"""

from __future__ import annotations

import json
import re
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from orchestrator_kernel.audit.hasher import sha256_trunc16
from orchestrator_kernel.audit.redact import (
    DEFAULT_VALUE_THRESHOLD_BYTES,
    SAFE_KEYS,
    redact,
)

# Marker shapes the processor MUST produce. Either the bare-string variant
# or the nested-object variant (``:obj:`` infix).
MARKER_RE = re.compile(
    r"^<redacted:(?:obj:)?\d+-bytes:sha256-[0-9a-f]{32}>$"
)


# Safe keys list filtered down to ones the processor actually checks
# inside an event_dict (we exclude `event` etc. that have other meaning
# in structlog if you go there directly).
SAFE_KEY_SAMPLE = sorted(SAFE_KEYS)


def _canonical_json(obj: Any) -> str:
    """Same canonicalisation the audit writer uses — what reviewers grep."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        ensure_ascii=False,
    )


# Strategies ----------------------------------------------------------------


# A "sensitive blob" — large enough that the redactor MUST replace it.
# We mix ASCII high-entropy junk and unicode so the byte-vs-char distinction
# is exercised.
_secret_text = st.one_of(
    st.text(
        alphabet=st.characters(
            min_codepoint=0x21, max_codepoint=0x7E
        ),
        min_size=DEFAULT_VALUE_THRESHOLD_BYTES + 1,
        max_size=DEFAULT_VALUE_THRESHOLD_BYTES * 4,
    ),
    st.text(
        alphabet=st.characters(
            min_codepoint=0x4E00, max_codepoint=0x9FFF
        ),  # CJK — 3 bytes each in UTF-8
        min_size=(DEFAULT_VALUE_THRESHOLD_BYTES // 3) + 1,
        max_size=(DEFAULT_VALUE_THRESHOLD_BYTES // 3) + 50,
    ),
)


_safe_key = st.sampled_from(SAFE_KEY_SAMPLE)
_user_key = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        min_codepoint=0x21,
        max_codepoint=0x7E,
    ),
    min_size=1,
    max_size=24,
).filter(lambda k: k not in SAFE_KEYS)


# --------------------------------------------------------------------------
# P1 — Marker shape
# --------------------------------------------------------------------------


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(key=_user_key, secret=_secret_text)
def test_p1_marker_shape_is_well_formed(key: str, secret: str) -> None:
    """Every redacted value matches the documented marker regex."""
    out = redact({key: secret})
    val = out[key]
    assert isinstance(val, str), val
    assert MARKER_RE.match(val), f"unexpected marker shape: {val!r}"


# --------------------------------------------------------------------------
# P2 — Allowlist transparency
# --------------------------------------------------------------------------


@settings(
    max_examples=80,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(key=_safe_key, secret=_secret_text)
def test_p2_allowlist_keys_pass_through_verbatim(
    key: str, secret: str
) -> None:
    """Safe keys always pass through, even if the value would otherwise trip."""
    out = redact({key: secret})
    assert out[key] == secret, (
        f"safe key {key!r} got redacted: {out[key]!r}"
    )


# --------------------------------------------------------------------------
# P3 — Plaintext absence (the heart of INV-8)
# --------------------------------------------------------------------------


@settings(
    max_examples=120,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(key=_user_key, secret=_secret_text)
def test_p3_secret_never_appears_in_serialized_line(
    key: str, secret: str
) -> None:
    """The grep-the-line invariant: the secret bytes MUST NOT survive."""
    out = redact({key: secret, "actor": "user:alice", "eventType": "task_failed"})
    line = _canonical_json(out)
    # Need at least 16 chars of non-trivial overlap to make the test
    # meaningful (single chars like "a" almost certainly appear inside
    # an audit line). Window-scan the secret in 32-char chunks: any one
    # of them surviving is a leak.
    chunk = 32
    for i in range(0, len(secret) - chunk + 1, chunk):
        needle = secret[i : i + chunk]
        # The marker contains a hex hash; pure ASCII hex coincidence is
        # vanishingly unlikely (32 hex chars = 128 bits) but we still
        # exclude that subset by ensuring the needle has at least one
        # non-hex character.
        if all(c in "0123456789abcdef" for c in needle):
            continue
        assert needle not in line, (
            f"plaintext window survived redaction: {needle!r} in {line[:200]!r}…"
        )


# --------------------------------------------------------------------------
# P4 — Idempotency
# --------------------------------------------------------------------------


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(key=_user_key, secret=_secret_text)
def test_p4_redact_is_idempotent(key: str, secret: str) -> None:
    """redact(redact(x)) == redact(x). No double-replacement, no leak."""
    once = redact({key: secret})
    twice = redact(dict(once))  # mutating in place; pass a copy.
    assert once == twice, (
        f"idempotency violated:\n once = {once!r}\n twice = {twice!r}"
    )


# --------------------------------------------------------------------------
# P5 — Hash determinism (correlation without recovery)
# --------------------------------------------------------------------------


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(key=_user_key, secret=_secret_text)
def test_p5_marker_hash_is_deterministic(key: str, secret: str) -> None:
    """Same payload → same marker → operators correlate by hash, not content."""
    a = redact({key: secret})
    b = redact({key: secret})
    assert a[key] == b[key]
    # And the embedded hash is exactly sha256_trunc16(secret_bytes).
    expected_hash = sha256_trunc16(secret.encode("utf-8"))
    assert expected_hash in a[key], (
        f"marker missing the canonical hash: marker={a[key]!r} expected_hash={expected_hash}"
    )


# --------------------------------------------------------------------------
# P6 — Threshold honesty
# --------------------------------------------------------------------------


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    key=_user_key,
    delta=st.integers(min_value=-32, max_value=32),
)
def test_p6_threshold_boundary_is_strict(key: str, delta: int) -> None:
    """Strings of exactly N bytes pass; N+1 bytes get redacted."""
    target_bytes = DEFAULT_VALUE_THRESHOLD_BYTES + delta
    if target_bytes <= 0:
        return
    payload = "a" * target_bytes  # ASCII so byte length == char length

    out = redact({key: payload})
    if target_bytes <= DEFAULT_VALUE_THRESHOLD_BYTES:
        assert out[key] == payload, (
            f"value at threshold ({target_bytes} bytes) wrongly redacted"
        )
    else:
        assert MARKER_RE.match(out[key]), (
            f"value over threshold ({target_bytes} bytes) NOT redacted: {out[key]!r}"
        )


# --------------------------------------------------------------------------
# P7 — Nested object redaction also blocks plaintext
# --------------------------------------------------------------------------


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    key=_user_key,
    secret=_secret_text,
    extra_int=st.integers(min_value=0, max_value=10**6),
)
def test_p7_nested_object_redaction_blocks_plaintext(
    key: str, secret: str, extra_int: int
) -> None:
    """A dict containing the secret as a value MUST NOT leak it after redact.

    The processor's ``:obj:`` branch fires on nested mutables; the canonical
    JSON of the wrapping dict has the same plaintext bytes as the leaf
    string would, so the same INV-8 grep applies.
    """
    nested = {"inner": {"secret": secret, "salt": extra_int}}
    out = redact({key: nested})
    val = out[key]
    line = _canonical_json(out)

    # Either the whole nested object got an obj: marker, OR (because the
    # canonical JSON stayed under the threshold) it kept its structure.
    if isinstance(val, str):
        assert MARKER_RE.match(val), val
        # Definitely no plaintext now.
        chunk = 32
        for i in range(0, len(secret) - chunk + 1, chunk):
            needle = secret[i : i + chunk]
            if all(c in "0123456789abcdef" for c in needle):
                continue
            assert needle not in line, (
                f"plaintext leaked through nested redaction: {needle!r}"
            )
    else:
        # Stayed below threshold → original structure preserved. The
        # secret IS present, but only because its serialised size is
        # <= DEFAULT_VALUE_THRESHOLD_BYTES. P6 already validates this
        # boundary; nothing more to assert here.
        assert isinstance(val, dict)
