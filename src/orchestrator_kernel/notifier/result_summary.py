"""ResultSummary builder + CLI notifier.

Originally landed in T046 as the minimal happy-path builder for US1; T083
(Phase 8) upgrades it with three additions:

1. **Redacted command digest** — ``build_command_digest`` now runs the input
   through the FR-020 redact pipeline before truncating to 256 chars, so
   long pasted secrets do not leak into the summary's user-facing field.
2. **``kernel_restarted`` outcome path** — :func:`build_kernel_restart_summary`
   constructs a schema-valid ``ResultSummary`` for traces compensated by
   :mod:`orchestrator_kernel.audit.scanner` after a crash. The message
   carries the schema-mandated "re-submit" + "NEW eventId" hint
   (validator-enforced).
3. **Contract-aligned default** — first-attempt deliveries now use
   ``deliveryAttempt=0`` (per JSON-Schema description "0 = first delivery";
   the previous T046 default of ``1`` was incorrect even though the
   pydantic ``le=3`` mirror happened to accept it).

``print_to_cli`` remains the MVP CLI sink; T084 ``notifier.delivery``
wraps it in the retry loop for FR-029 / SC-010.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, TextIO

from ..audit.redact import redact
from ..contracts.result_summary import LeafResult, ResultSummary, TraceOutcome
from ..contracts.task import Task

_DIGEST_MAX_CHARS = 256

KERNEL_RESTART_MESSAGE = (
    "system interrupted by kernel restart; please re-submit your command "
    "with a NEW eventId."
)
"""Canonical FR-029 message body. Contains the schema-required 're-submit' and
'NEW eventId' substrings enforced by ResultSummary._enforce_restart_hint."""


def build_command_digest(text: str, *, max_chars: int = _DIGEST_MAX_CHARS) -> str:
    """Produce a redact-clean, length-capped preview of the original text.

    The full text is first run through :func:`audit.redact.redact` (which
    short-circuits any single oversized string into ``<redacted:n-bytes:...>``)
    so we never echo back a paste that was rejected by the audit layer.
    Whitespace is then collapsed and the result is truncated to ``max_chars``.
    """
    redacted = redact({"_text": text})["_text"]
    if not isinstance(redacted, str):
        redacted = str(redacted)
    collapsed = " ".join(redacted.split())
    if len(collapsed) > max_chars:
        return collapsed[: max_chars - 1] + "\u2026"
    return collapsed


def compute_trace_outcome(leaves: Sequence[Task]) -> TraceOutcome:
    """Roll a per-leaf outcome list into the 7-value ``TraceOutcome`` enum.

    Precedence (highest first):
      1. ``kernel_restarted`` never produced here; reserved for T082.
      2. any leaf in ``cancelled`` -> ``cancelled``
      3. any leaf in ``denied`` / ``denied_by_timeout`` -> ``denied``
      4. all leaves ``succeeded`` -> ``all_succeeded``
      5. all leaves ``failed`` -> ``all_failed``
      6. otherwise -> ``partial_failed``
    """
    if not leaves:
        raise ValueError("cannot summarize trace with zero leaves")

    outcomes = [leaf.outcome for leaf in leaves]
    if any(o is None for o in outcomes):
        raise ValueError(
            "compute_trace_outcome requires every leaf to have reached a terminal "
            f"outcome; got {outcomes}"
        )

    if any(o == "cancelled" for o in outcomes):
        return "cancelled"
    if any(o in ("denied", "denied_by_timeout") for o in outcomes):
        return "denied"
    if all(o == "succeeded" for o in outcomes):
        return "all_succeeded"
    if all(o == "failed" for o in outcomes):
        return "all_failed"
    return "partial_failed"


def _leaf_to_result(task: Task) -> LeafResult:
    if task.capability is None:
        raise ValueError(
            f"leaf_action task {task.taskId!r} MUST have capability; found None"
        )
    if task.outcome is None:
        raise ValueError(
            f"cannot summarize leaf {task.taskId!r} with outcome=None"
        )
    return LeafResult(
        taskId=task.taskId,
        capability=task.capability,
        outcome=task.outcome,
        failureReason=task.failureReason,
        resultHash=task.resultHash,
    )


def _build_message(
    trace_outcome: TraceOutcome,
    leaves: Sequence[Task],
    leaf_outputs: Mapping[str, dict[str, Any]] | None,
) -> str:
    """Human-readable one-liner. ``kernel_restarted`` reserved for T083."""
    if trace_outcome == "all_succeeded":
        parts: list[str] = []
        if leaf_outputs is not None:
            for leaf in leaves:
                output = leaf_outputs.get(leaf.taskId) or {}
                text = output.get("text")
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return " | ".join(parts)
        return "all leaf tasks succeeded"
    if trace_outcome == "all_failed":
        reasons = sorted(
            {leaf.failureReason for leaf in leaves if leaf.failureReason}
        )
        if reasons:
            return f"all leaf tasks failed: {', '.join(reasons)}"
        return "all leaf tasks failed"
    if trace_outcome == "partial_failed":
        return "some leaf tasks failed; see leafResults"
    if trace_outcome == "cancelled":
        return "trace cancelled by user"
    if trace_outcome == "denied":
        return "trace denied (approval declined or timed out)"
    return "trace reached terminal state"


def build_result_summary(
    *,
    trace_id: str,
    event_id: str,
    user_id: str,
    command_text: str,
    leaves: Sequence[Task],
    leaf_outputs: Mapping[str, dict[str, Any]] | None = None,
    prepared_at: datetime | None = None,
    delivery_attempt: int = 0,
) -> ResultSummary:
    """Assemble a ``ResultSummary`` pydantic model (not yet delivered).

    Args:
        trace_id / event_id / user_id: identifiers captured from the
            original ``EntryEvent`` + idempotency layer.
        command_text: raw user command; redacted then truncated to 256
            chars for ``commandDigest``.
        leaves: terminal-state leaf Tasks (ordered; MVP passes exactly one).
        leaf_outputs: optional map ``{taskId: worker_result_output}``
            captured from ``ResultFrame.output`` so the human-readable
            ``message`` can echo back the worker's actual payload.
        prepared_at: optional clock override (tests inject a fixed time).
        delivery_attempt: 0 on first-try (matches contract); T084 mutates
            this on retries.
    """
    if not leaves:
        raise ValueError("ResultSummary requires at least one leaf result")

    trace_outcome = compute_trace_outcome(leaves)
    summary = ResultSummary(
        kind="result_summary",
        traceId=trace_id,
        eventId=event_id,
        userId=user_id,
        commandDigest=build_command_digest(command_text),
        traceOutcome=trace_outcome,
        leafResults=[_leaf_to_result(leaf) for leaf in leaves],
        message=_build_message(trace_outcome, leaves, leaf_outputs),
        preparedAt=prepared_at if prepared_at is not None else datetime.now(tz=UTC),
        deliveryAttempt=delivery_attempt,
    )
    return summary


def build_kernel_restart_summary(
    *,
    trace_id: str,
    event_id: str | None,
    user_id: str | None,
    command_text: str | None,
    affected_task_ids: Iterable[str],
    capability_hint: str = "unknown",
    prepared_at: datetime | None = None,
    delivery_attempt: int = 0,
) -> ResultSummary:
    """Build a ``ResultSummary`` for a trace caught by the audit scanner (T082).

    The crash-recovery path has different invariants from the happy path:

    * The kernel does NOT have ``Task`` objects in memory anymore — only
      the audit-derived task IDs from :class:`AutoFailedTrace`.
    * ``traceOutcome`` is hard-coded to ``kernel_restarted`` so the schema
      validator enforces the FR-029 message wording.
    * ``userId`` and ``eventId`` may be missing from the audit log
      (event_received was never recorded for that trace, or the user
      identifier was redacted). We fall back to placeholders that still
      satisfy the schema's ``min_length=16`` constraint on ``eventId``.

    Args:
        trace_id: the surviving traceId from the audit scan.
        event_id: best-effort original eventId; ``None`` means we synthesize
            a placeholder so the schema accepts the model.
        user_id: best-effort userId; ``None`` falls back to ``"unknown"``.
        command_text: best-effort original command; ``None`` falls back to
            an empty string (digest will still serialize).
        affected_task_ids: leaf task IDs that the scanner flagged as
            in-flight at crash time.
        capability_hint: the capability name to attach to each synthetic
            ``LeafResult`` when the audit log doesn't carry it.
        prepared_at: clock override for tests.
        delivery_attempt: 0 on first-try; T084 owns increments.
    """
    leaves = [
        LeafResult(
            taskId=task_id,
            capability=capability_hint,
            outcome="failed",
            failureReason="kernel_restart",
        )
        for task_id in affected_task_ids
    ]
    if not leaves:
        raise ValueError(
            "kernel-restart summary needs at least one affected task id"
        )

    safe_event_id = event_id if event_id else f"recovered-{trace_id}"[:64]
    safe_user_id = user_id if user_id else "unknown"
    digest_source = command_text if command_text else "(original command unavailable)"

    return ResultSummary(
        kind="result_summary",
        traceId=trace_id,
        eventId=safe_event_id,
        userId=safe_user_id,
        commandDigest=build_command_digest(digest_source),
        traceOutcome="kernel_restarted",
        leafResults=leaves,
        message=KERNEL_RESTART_MESSAGE,
        preparedAt=prepared_at if prepared_at is not None else datetime.now(tz=UTC),
        deliveryAttempt=delivery_attempt,
    )


def print_to_cli(summary: ResultSummary, *, stream: TextIO | None = None) -> None:
    """MVP delivery: write one JSON line to stdout (or the given text stream).

    The line matches ``contracts/result-summary.schema.json`` so programmatic
    consumers can parse it with the same ``ResultSummary`` pydantic model.
    """
    out = stream if stream is not None else sys.stdout
    out.write(summary.model_dump_json(exclude_none=True) + "\n")
    out.flush()
