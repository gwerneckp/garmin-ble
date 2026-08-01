"""Daily step count.

Service code: ``REALTIME_STEPS`` (7).

Wire format (MLR payload)::

    [steps (uint32 LE), goal (uint32 LE)]
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

from ..constants import GarminService
from .base import Reading, register


@dataclass(frozen=True)
class Steps(Reading):
    """Steps taken today, against the user's daily goal."""

    count: int
    goal: int

    @property
    def fraction_of_goal(self) -> float:
        """Progress toward the goal in the range 0.0-1.0+ (0.0 if no goal set)."""
        return self.count / self.goal if self.goal else 0.0

    def __str__(self) -> str:
        return f"{self.count:,} / {self.goal:,} steps ({self.fraction_of_goal:.0%})"


def parse(data: bytes) -> Optional[Steps]:
    if len(data) < 8:
        return None
    count, goal = struct.unpack("<II", data[:8])
    return Steps(count=count, goal=goal)

def encode(reading: Steps) -> bytes:
    """Inverse of :func:`parse`, for the simulator and round-trip tests."""
    return struct.pack("<II", reading.count, reading.goal)



STEPS = register(
    "steps", GarminService.REALTIME_STEPS, Steps, parse, encode,
)
