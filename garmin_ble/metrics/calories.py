"""Calories burned today.

Service code: ``REALTIME_CALORIES`` (8).

Wire format (MLR payload)::

    [total (uint32 LE), active (uint32 LE)]
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

from ..constants import GarminService
from .base import Reading, register


@dataclass(frozen=True)
class Calories(Reading):
    """Total and active kilocalories burned today."""

    total: int
    active: int

    @property
    def resting(self) -> int:
        """Calories attributable to basal metabolism rather than activity."""
        return max(self.total - self.active, 0)

    def __str__(self) -> str:
        return f"{self.total:,} kcal total ({self.active:,} active)"


def parse(data: bytes) -> Optional[Calories]:
    if len(data) < 8:
        return None
    total, active = struct.unpack("<II", data[:8])
    return Calories(total=total, active=active)

def encode(reading: Calories) -> bytes:
    """Inverse of :func:`parse`, for the simulator and round-trip tests."""
    return struct.pack("<II", reading.total, reading.active)



CALORIES = register(
    "calories", GarminService.REALTIME_CALORIES, Calories, parse, encode,
    "Total and active kilocalories burned today",
)
