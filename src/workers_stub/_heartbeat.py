"""Shared heartbeat-emitter helper for every stub worker (FR-009 / T076).

Each real / stub worker is expected to send one ``HeartbeatFrame`` on
stdout every ~500 ms so the kernel's :class:`HeartbeatTracker` sees the
worker as alive. This module owns the single implementation so every
worker can call ``start_heartbeat_thread(worker_id, stdout_lock)`` in
its ``main()``.

Design notes:

* The thread writes directly to ``sys.stdout.buffer`` under the caller's
  lock; callers MUST hold that lock whenever they emit non-heartbeat
  frames so two writers never interleave a JSON line.
* The thread is a **daemon**: if ``main()`` returns for any reason the
  interpreter exits and the thread dies with it. Tests rely on this to
  ensure crash paths don't leak heartbeat threads.
* Errors on write (``BrokenPipeError``, ``OSError``) silently stop the
  thread — the kernel has already closed our stdout so the worker is
  effectively dying anyway.
* Consumers can disable emission by setting the environment variable
  ``<prefix>_DISABLE_HEARTBEAT=1`` (e.g. ``SLEEP_WORKER_DISABLE_HEARTBEAT=1``)
  so a single worker can be turned into a "silent" stub for
  unhealthy-flip integration tests without a dedicated file.
"""

from __future__ import annotations

import os
import sys
import threading
from datetime import UTC, datetime

from orchestrator_kernel.contracts.worker_protocol import HeartbeatFrame

DEFAULT_HEARTBEAT_INTERVAL_S = 0.5


def _emit_locked(frame_json: str, stdout_lock: threading.Lock) -> None:
    if "\n" in frame_json:
        raise RuntimeError("heartbeat thread tried to emit embedded newline")
    with stdout_lock:
        try:
            sys.stdout.buffer.write(frame_json.encode("utf-8") + b"\n")
            sys.stdout.buffer.flush()
        except (BrokenPipeError, OSError):
            # Stdout is gone — stop emitting silently.
            raise


def _heartbeat_loop(
    worker_id: str,
    stdout_lock: threading.Lock,
    interval_s: float,
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        frame = HeartbeatFrame(
            kind="heartbeat",
            workerId=worker_id,
            timestamp=datetime.now(tz=UTC),
            activeTaskIds=[],
        )
        try:
            _emit_locked(
                frame.model_dump_json(exclude_none=True), stdout_lock
            )
        except (BrokenPipeError, OSError):
            return
        if stop_event.wait(timeout=interval_s):
            return


def start_heartbeat_thread(
    *,
    worker_id: str,
    stdout_lock: threading.Lock,
    env_disable_key: str | None = None,
    interval_s: float = DEFAULT_HEARTBEAT_INTERVAL_S,
) -> tuple[threading.Thread | None, threading.Event]:
    """Spawn a daemon thread emitting ``HeartbeatFrame`` every ``interval_s``.

    Returns ``(thread, stop_event)``. The thread is already ``start()``ed
    unless ``env_disable_key`` is set and present in the environment —
    in which case ``(None, stop_event)`` is returned and the worker runs
    without heartbeats (useful for the "silent worker" unhealthy-flip
    scenario).
    """
    stop_event = threading.Event()
    if env_disable_key and os.environ.get(env_disable_key):
        return None, stop_event
    thread = threading.Thread(
        target=_heartbeat_loop,
        args=(worker_id, stdout_lock, interval_s, stop_event),
        name=f"heartbeat-{worker_id}",
        daemon=True,
    )
    thread.start()
    return thread, stop_event
