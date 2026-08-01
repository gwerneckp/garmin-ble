"""Stress level.

Service code: ``REALTIME_STRESS`` (13).

Wire format (MLR payload)::

    [level (int8)]

Garmin reports 0-100, where higher is more stressed. Negative values mean the
watch cannot compute a score right now (too much movement, no HRV data).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

from ..constants import GarminService
from .base import Reading, register


@dataclass(frozen=True)
class Stress(Reading):
    """Stress score from 0 (rested) to 100 (highly stressed)."""

    level: int

    @property
    def band(self) -> str:
        """Garmin's own four-band labelling of the score."""
        if self.level < 26:
            return "rest"
        if self.level < 51:
            return "low"
        if self.level < 76:
            return "medium"
        return "high"

    def __str__(self) -> str:
        return f"{self.level}/100 ({self.band})"


def parse(data: bytes) -> Optional[Stress]:
    if len(data) < 1:
        return None
    return Stress(level=struct.unpack("<b", bytes([data[0]]))[0])

def encode(reading: Stress) -> bytes:
    """Inverse of :func:`parse`, for the simulator and round-trip tests."""
    return struct.pack("<b", reading.level)



STRESS = register(
    "stress", GarminService.REALTIME_STRESS, Stress, parse, encode,
    "Stress score from 0 to 100",
)
