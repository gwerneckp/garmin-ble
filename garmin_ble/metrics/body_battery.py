"""Body Battery energy level.

Service code: ``REALTIME_BODY_BATTERY`` (20).

Wire format (MLR payload)::

    [level (int8)]
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

from ..constants import GarminService
from .base import Reading, register


@dataclass(frozen=True)
class BodyBattery(Reading):
    """Body Battery reserve from 0 (depleted) to 100 (fully charged)."""

    level: int

    def __str__(self) -> str:
        return f"{self.level}/100 body battery"


def parse(data: bytes) -> Optional[BodyBattery]:
    if len(data) < 1:
        return None
    return BodyBattery(level=struct.unpack("<b", bytes([data[0]]))[0])

def encode(reading: BodyBattery) -> bytes:
    """Inverse of :func:`parse`, for the simulator and round-trip tests."""
    return struct.pack("<b", reading.level)



BODY_BATTERY = register(
    "body_battery", GarminService.REALTIME_BODY_BATTERY, BodyBattery, parse, encode,
)
