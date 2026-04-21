"""T042 — Kernel <-> Worker stdio JSON-Lines protocol codec.

Wire format (contracts/worker-protocol.schema.json):
- one JSON object per line, UTF-8 encoded
- lines terminated by a single LF (`\\n`); CRLF tolerated on read
- no embedded newlines inside values, no trailing whitespace
- one of 7 discriminated frame kinds (see `contracts.worker_protocol`)

This module provides:

- `encode_frame()` / `decode_frame()` — pure byte-level codec usable from
  sync tests (see `tests/integration/test_worker_stdio_roundtrip.py` for
  the roundtrip invariants this preserves);
- `read_frames()` / `write_frame()` — async helpers for the real kernel
  loop, typed against `asyncio.StreamReader` / `StreamWriter` (which are
  exactly what `asyncio.create_subprocess_exec` hands back).

Malformed input (not-UTF-8 / not-JSON / fails pydantic discriminator) raises
`ProtocolFrameError`. The async reader logs and **continues** rather than
crashing the parent event loop, matching T038 test 3's parent-survival
requirement. Callers wanting strict failure semantics can wrap iteration
themselves.
"""

from __future__ import annotations

import json
from asyncio import StreamReader, StreamWriter
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, ValidationError

from ..contracts.worker_protocol import AnyWorkerFrame, WorkerFrame

FrameLike = AnyWorkerFrame | BaseModel | dict[str, Any]
"""Anything a caller can reasonably hand us: a validated pydantic frame,
an ad-hoc BaseModel subclass, or a raw dict that still needs serialization."""


class ProtocolFrameError(Exception):
    """A frame on the wire could not be decoded into a valid WorkerFrame.

    Carries `raw` (the offending bytes, truncated to 256 for safety) so the
    parent can audit without logging unbounded worker output.
    """

    def __init__(self, message: str, *, raw: bytes | str | None = None) -> None:
        super().__init__(message)
        self.raw = _clip(raw) if raw is not None else None


def _clip(blob: bytes | str, *, limit: int = 256) -> bytes | str:
    if isinstance(blob, bytes):
        return blob[:limit]
    return blob[:limit]


def encode_frame(frame: FrameLike) -> bytes:
    """Serialize one frame to UTF-8 JSON-Lines bytes, LF-terminated.

    Pydantic models use `model_dump_json(exclude_none=True)` so optional
    fields (`resultHash`, `failureReason`, ...) don't leak `null` onto the
    wire. Dict inputs are dumped with the minimal-separator form to keep
    the frame on exactly one line.
    """
    if isinstance(frame, BaseModel):
        body = frame.model_dump_json(exclude_none=True)
    else:
        body = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
    if "\n" in body:
        raise ProtocolFrameError(
            "encoded frame contains embedded newline (would break JSON-Lines framing)",
            raw=body,
        )
    return (body + "\n").encode("utf-8")


def decode_frame(line: bytes | str) -> AnyWorkerFrame:
    """Parse one wire line into a validated `AnyWorkerFrame`.

    Trailing CR / LF is tolerated on input (the echo-worker stub currently
    emits LF only, but some Windows shells inject CRLF when proxying
    subprocess I/O).
    """
    if isinstance(line, bytes):
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolFrameError(
                f"frame is not valid UTF-8: {exc}", raw=line
            ) from exc
    else:
        text = line

    stripped = text.rstrip("\r\n")
    if not stripped.strip():
        raise ProtocolFrameError("empty frame", raw=line)

    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ProtocolFrameError(
            f"frame is not valid JSON: {exc.msg}", raw=line
        ) from exc

    try:
        return WorkerFrame.validate_python(obj)
    except ValidationError as exc:
        raise ProtocolFrameError(
            f"frame fails WorkerFrame contract: {exc.error_count()} error(s)",
            raw=line,
        ) from exc


async def read_frames(reader: StreamReader) -> AsyncIterator[AnyWorkerFrame]:
    """Yield one validated frame per stdout line until EOF.

    Per T038 test 3, a malformed frame from a Worker MUST NOT crash the
    parent: we skip it and continue. Callers that want richer telemetry
    should use `decode_frame()` directly and handle `ProtocolFrameError`.
    """
    while True:
        line = await reader.readline()
        if not line:
            return
        try:
            yield decode_frame(line)
        except ProtocolFrameError:
            continue


async def write_frame(writer: StreamWriter, frame: FrameLike) -> None:
    """Encode `frame` and flush it onto `writer` as one line."""
    writer.write(encode_frame(frame))
    await writer.drain()
