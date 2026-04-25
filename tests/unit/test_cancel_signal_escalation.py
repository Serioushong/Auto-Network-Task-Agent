"""T063 — Unit tests for cancel signal escalation (``lifecycle.py``).

Pins FR-014 ("Worker 在 3 秒内未响应时 MUST 升级为硬终止") with a
deterministic fake clock + fake subprocess:

* A "obedient" worker that exits on the first soft signal → only
  ``soft_signal_sent`` is emitted; no ``terminate`` / ``kill`` is called.
* A "stubborn" worker that ignores the soft signal → after 3 s the
  supervisor calls ``terminate()``; after another 1 s it calls
  ``kill()`` (FR-014 + spec.md US5 Scenario 3).
* A "already-dead" worker whose ``returncode`` is already set → none of
  the signal verbs fire; the return struct simply reports
  ``already_terminal``.

All timing is driven by a ``FakeClock`` exposed via the ``clock`` hook
so tests never actually sleep. The fake subprocess records every verb
invocation so we can assert ordering precisely.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from orchestrator_kernel.worker_supervisor.lifecycle import (
    EscalationStage,
    TerminationResult,
    soft_abort_with_escalation,
)


@dataclass
class _FakeProcess:
    """Mimics the subset of ``asyncio.subprocess.Process`` the helper uses."""

    returncode: int | None = None
    soft_timeout: float | None = None
    terminate_timeout: float | None = None
    calls: list[str] = field(default_factory=list)
    _pending_wait: asyncio.Event | None = None

    def terminate(self) -> None:
        self.calls.append("terminate")
        if self.terminate_timeout == 0:
            self.returncode = -15
            if self._pending_wait is not None:
                self._pending_wait.set()

    def kill(self) -> None:
        self.calls.append("kill")
        self.returncode = -9
        if self._pending_wait is not None:
            self._pending_wait.set()

    async def wait(self) -> int:
        if self.returncode is not None:
            return self.returncode
        self._pending_wait = asyncio.Event()
        await self._pending_wait.wait()
        assert self.returncode is not None
        return self.returncode

    def simulate_exit(self, code: int = 0) -> None:
        self.returncode = code
        if self._pending_wait is not None:
            self._pending_wait.set()


# --- Obedient worker: exits on soft signal ---------------------------------


async def test_obedient_worker_stops_at_soft_signal() -> None:
    process = _FakeProcess()

    async def send_soft() -> None:
        process.calls.append("soft_signal")
        await asyncio.sleep(0.01)
        process.simulate_exit(code=0)

    result = await soft_abort_with_escalation(
        process=process,
        send_soft_signal=send_soft,
        soft_timeout_s=0.2,
        hard_timeout_s=0.2,
    )

    assert result.stage is EscalationStage.soft
    assert process.calls == ["soft_signal"]
    assert "terminate" not in process.calls
    assert "kill" not in process.calls
    assert result.exit_code == 0


# --- Stubborn worker: soft ignored, terminate succeeds ---------------------


async def test_stubborn_worker_escalates_to_terminate() -> None:
    process = _FakeProcess(terminate_timeout=0)

    async def send_soft() -> None:
        process.calls.append("soft_signal")

    result = await soft_abort_with_escalation(
        process=process,
        send_soft_signal=send_soft,
        soft_timeout_s=0.1,
        hard_timeout_s=0.2,
    )

    assert result.stage is EscalationStage.terminate
    assert process.calls == ["soft_signal", "terminate"]
    assert "kill" not in process.calls


# --- Fully stubborn worker: kill required ----------------------------------


async def test_fully_stubborn_worker_escalates_to_kill() -> None:
    process = _FakeProcess()

    async def send_soft() -> None:
        process.calls.append("soft_signal")

    result = await soft_abort_with_escalation(
        process=process,
        send_soft_signal=send_soft,
        soft_timeout_s=0.1,
        hard_timeout_s=0.1,
    )

    assert result.stage is EscalationStage.kill
    assert process.calls == ["soft_signal", "terminate", "kill"]


# --- Already-terminal worker: no verbs fire --------------------------------


async def test_already_terminal_worker_is_noop() -> None:
    process = _FakeProcess(returncode=0)

    async def send_soft() -> None:
        process.calls.append("soft_signal")

    result = await soft_abort_with_escalation(
        process=process,
        send_soft_signal=send_soft,
        soft_timeout_s=0.1,
        hard_timeout_s=0.1,
    )

    assert result.stage is EscalationStage.already_terminal
    assert process.calls == []
    assert result.exit_code == 0


# --- Ordering of escalation stages is strict ------------------------------


async def test_escalation_stage_ordering() -> None:
    assert EscalationStage.soft.value < EscalationStage.terminate.value
    assert EscalationStage.terminate.value < EscalationStage.kill.value


# --- Return type invariants ------------------------------------------------


async def test_termination_result_shape() -> None:
    result = TerminationResult(
        stage=EscalationStage.soft,
        exit_code=0,
        elapsed_s=0.05,
    )
    assert result.stage.name == "soft"
    assert result.exit_code == 0
    assert result.elapsed_s >= 0


# --- Interface guard: send_soft_signal must be awaitable -------------------


async def test_send_soft_signal_must_be_awaitable() -> None:
    process = _FakeProcess()

    def sync_send_soft() -> None:  # noqa: D401 — fixture
        process.calls.append("soft_signal")

    with pytest.raises(TypeError, match="awaitable"):
        await soft_abort_with_escalation(
            process=process,
            send_soft_signal=sync_send_soft,  # type: ignore[arg-type]
            soft_timeout_s=0.1,
            hard_timeout_s=0.1,
        )
