"""Heart rate variability, as raw RR-intervals.

Service code: ``REALTIME_HRV`` (12).

Wire format (MLR payload)::

    [rr (uint16 LE), ...]

One packet carries one beat-to-beat interval. Aggregating these into an HRV
score (RMSSD, SDNN, …) is the caller's job — the watch does not send one.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

from ..constants import GarminService
from .base import Reading, register


@dataclass(frozen=True)
class Hrv(Reading):
    """One RR-interval: the gap between two consecutive heartbeats."""

    rr_ms: int

    @property
    def instantaneous_bpm(self) -> float:
        """Heart rate implied by this single interval."""
        return 60_000.0 / self.rr_ms if self.rr_ms else 0.0

    def __str__(self) -> str:
        return f"{self.rr_ms} ms RR ({self.instantaneous_bpm:.0f} bpm)"


def parse(data: bytes) -> Optional[Hrv]:
    if len(data) < 2:
        return None
    return Hrv(rr_ms=struct.unpack("<H", data[:2])[0])

def encode(reading: Hrv) -> bytes:
    """Inverse of :func:`parse`, for the simulator and round-trip tests."""
    return struct.pack("<H", reading.rr_ms)



HRV = register(
    "hrv", GarminService.REALTIME_HRV, Hrv, parse, encode,
)
