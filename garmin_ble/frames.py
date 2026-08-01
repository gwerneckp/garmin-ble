"""Wire-frame tracing.

Every byte in and out of the transport becomes a :class:`Frame`. Frames feed
three things: ``@watch.on_frame`` for live inspection, ``watch.record(path)``
for capture files, and the counters behind ``watch.diagnostics``.

Capture files are JSON Lines — one frame per line, greppable with ordinary
tools, and replayable with ``Watch.replay(path)``. Attaching one to a bug
report lets someone reproduce a protocol problem without owning the watch.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import IO, Iterator, Optional, Union


class Direction(str, Enum):
    """Which way a frame travelled."""

    TX = "tx"  # host -> watch
    RX = "rx"  # watch -> host

    def __str__(self) -> str:
        return self.value


class FrameKind(str, Enum):
    """What the frame turned out to be, once routed."""

    CONTROL = "control"      # MLR handle 0x00: CLOSE_ALL / REGISTER_ML
    GFDI = "gfdi"            # COBS-framed GFDI message
    TELEMETRY = "telemetry"  # real-time sensor payload
    UNKNOWN = "unknown"      # arrived on a handle we have no mapping for

    def __str__(self) -> str:
        return self.value


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Frame:
    """One transport-level packet, with whatever decoding succeeded."""

    direction: Direction
    kind: FrameKind
    handle: int
    raw: bytes
    decoded: Optional[str] = None
    at: datetime = field(default_factory=_now, compare=False)

    def __str__(self) -> str:
        body = self.decoded or self.raw.hex()
        return f"{self.direction} {self.kind:<9} handle=0x{self.handle:02x} {body}"

    # ── capture-file serialisation ──────────────────────────────────────────

    def to_json(self) -> str:
        payload = {
            "at": self.at.isoformat(),
            "direction": self.direction.value,
            "kind": self.kind.value,
            "handle": self.handle,
            "hex": self.raw.hex(),
        }
        if self.decoded:
            payload["decoded"] = self.decoded
        return json.dumps(payload)

    @classmethod
    def from_json(cls, line: str) -> "Frame":
        d = json.loads(line)
        return cls(
            direction=Direction(d["direction"]),
            kind=FrameKind(d.get("kind", "unknown")),
            handle=int(d.get("handle", 0)),
            raw=bytes.fromhex(d["hex"]),
            decoded=d.get("decoded"),
            at=datetime.fromisoformat(d["at"]),
        )


class Recorder:
    """Appends frames to a capture file.

    Opened lazily so that constructing a recorder for a session that never
    connects does not leave an empty file behind.
    """

    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)
        self._fh: Optional[IO[str]] = None
        self.count = 0

    def write(self, frame: Frame) -> None:
        if self._fh is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.path.open("w", encoding="utf-8")
        self._fh.write(frame.to_json() + "\n")
        self._fh.flush()
        self.count += 1

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def read_capture(path: Union[str, Path]) -> Iterator[Frame]:
    """Yield frames from a capture file written by :class:`Recorder`."""
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield Frame.from_json(line)


__all__ = ["Direction", "FrameKind", "Frame", "Recorder", "read_capture"]
