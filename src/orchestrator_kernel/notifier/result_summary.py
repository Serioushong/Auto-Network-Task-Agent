"""T046 — Minimal ResultSummary builder + CLI front-print notifier (US1 MVP).

Builds a ``ResultSummary`` pydantic model from a single-leaf trace's terminal
state, aggregates the per-leaf outcomes into the ``traceOutcome`` roll-up
(FR-029), and offers a simple ``print_to_cli`` helper for the CLI channel so
US1 can satisfy FR-030 ("do not force the user to poll") at the lowest
possible complexity.

This module intentionally **does NOT** implement retry / delivery-attempt
state — that lands in T084 (``notifier.delivery``). MVP always records
``deliveryAttempt=1`` on first-try-successful paths.

Design decisions:

- ``command_digest`` is the original EntryEvent text, redacted-and-truncated
  to 256 characters. No hashing here; the audit subsystem keeps its
  ``input_hash`` / ``output_hash`` fields, this digest is for user-readable
  recall.
- Trace outcome logic for MVP only has to distinguish ``all_succeeded``,
  ``all_failed`` (single-leaf), and ``partial_failed`` (future multi-leaf).
  ``cancelled`` / ``denied`` / ``rejected`` / ``kernel_restarted`` are
  pre-wired so the extension tasks (T057 / T065 / T082) can reuse the
  aggregator without refactor.
- ``print_to_cli`` emits one line to ``sys.stdout`` in the CLI channel's
  expected JSON form (same shape as the pydantic dump) so programmatic
  consumers can parse it; later (T097) the HTTP entry will replace the
  ``sys.stdout`` write with a response body.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, TextIO

from ..contracts.result_summary import LeafResult, ResultSummary, TraceOutcome
from ..contracts.task import Task

_DIGEST_MAX_CHARS = 256


def build_command_digest(text: str, *, max_chars: int = _DIGEST_MAX_CHARS) -> str:
    """Produce a human-readable, length-capped preview of the original text.

    MVP treats the text as already-safe (CLI submits it; no third-party
    escaping required). US6 (T083) replaces this with a proper ``redact``
    pipeline that strips any PII / secret-ish substrings before the cap.
    """
    collapsed = " ".join(text.split())
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
    delivery_attempt: int = 1,
) -> ResultSummary:
    """Assemble a ``ResultSummary`` pydantic model (not yet delivered).

    Args:
        trace_id / event_id / user_id: identifiers captured from the
            original ``EntryEvent`` + idempotency layer.
        command_text: raw user command; truncated to 256 chars for
            ``commandDigest``.
        leaves: terminal-state leaf Tasks (ordered; MVP passes exactly one).
        leaf_outputs: optional map ``{taskId: worker_result_output}``
            captured from ``ResultFrame.output`` so the human-readable
            ``message`` can echo back the worker's actual payload.
        prepared_at: optional clock override (tests inject a fixed time).
        delivery_attempt: 1 on first-try-successful paths (US1). T084 owns
            values 2 / 3 on retries.
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


def print_to_cli(summary: ResultSummary, *, stream: TextIO | None = None) -> None:
    """MVP delivery: write one JSON line to stdout (or the given text stream).

    The line matches ``contracts/result-summary.schema.json`` so programmatic
    consumers can parse it with the same ``ResultSummary`` pydantic model.
    """
    out = stream if stream is not None else sys.stdout
    out.write(summary.model_dump_json(exclude_none=True) + "\n")
    out.flush()
