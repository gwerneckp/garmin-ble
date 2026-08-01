"""Heart rate.

Service code: ``REALTIME_HR`` (6).

Wire format (service payload, after the MLR routing byte)::

    [padding, hr, resting_hr]

``resting_hr`` is 0 when the watch has not established one yet; that is
surfaced as ``None`` rather than a misleading zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..constants import GarminService
from .base import Reading, register


@dataclass(frozen=True)
class HeartRate(Reading):
    """Current heart rate in beats per minute."""

    bpm: int
    resting_bpm: Optional[int] = None

    def __str__(self) -> str:
        resting = "unknown" if self.resting_bpm is None else f"{self.resting_bpm}"
        return f"{self.bpm} bpm (resting {resting})"


def parse(data: bytes) -> Optional[HeartRate]:
    if len(data) < 3:
        return None
    resting = data[2]
    return HeartRate(bpm=data[1], resting_bpm=resting if resting > 0 else None)

def encode(reading: HeartRate) -> bytes:
    """Inverse of :func:`parse`, for the simulator and round-trip tests."""
    return bytes([0, reading.bpm, reading.resting_bpm or 0])



HEART_RATE = register(
    "heart_rate", GarminService.REALTIME_HR, HeartRate, parse, encode,
)
