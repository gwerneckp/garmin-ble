"""Intensity minutes.

Service code: ``REALTIME_INTENSITY`` (10).

Wire format (MLR payload)::

    [moderate (uint16 LE), vigorous (uint16 LE)]
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

from ..constants import GarminService
from .base import Reading, register


@dataclass(frozen=True)
class Intensity(Reading):
    """Weekly moderate and vigorous intensity minutes."""

    moderate: int
    vigorous: int

    @property
    def total(self) -> int:
        """Garmin scores vigorous minutes double toward the weekly goal."""
        return self.moderate + 2 * self.vigorous

    def __str__(self) -> str:
        return f"{self.moderate} moderate + {self.vigorous} vigorous min"


def parse(data: bytes) -> Optional[Intensity]:
    if len(data) < 4:
        return None
    moderate, vigorous = struct.unpack("<HH", data[:4])
    return Intensity(moderate=moderate, vigorous=vigorous)

def encode(reading: Intensity) -> bytes:
    """Inverse of :func:`parse`, for the simulator and round-trip tests."""
    return struct.pack("<HH", reading.moderate, reading.vigorous)



INTENSITY = register(
    "intensity", GarminService.REALTIME_INTENSITY, Intensity, parse, encode,
    "Weekly moderate and vigorous intensity minutes",
)
