"""Respiration rate.

Service code: ``REALTIME_RESPIRATION`` (21).

Wire format (MLR payload)::

    [breaths_per_min (int8)]

Values ``<= 0`` mean the sensor has no reading yet and yield nothing.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

from ..constants import GarminService
from .base import Reading, register


@dataclass(frozen=True)
class Respiration(Reading):
    """Breathing rate in breaths per minute."""

    breaths_per_min: int

    def __str__(self) -> str:
        return f"{self.breaths_per_min} breaths/min"


def parse(data: bytes) -> Optional[Respiration]:
    if len(data) < 1:
        return None
    breaths = struct.unpack("<b", bytes([data[0]]))[0]
    if breaths <= 0:
        return None
    return Respiration(breaths_per_min=breaths)

def encode(reading: Respiration) -> bytes:
    """Inverse of :func:`parse`, for the simulator and round-trip tests."""
    return struct.pack("<b", reading.breaths_per_min)



RESPIRATION = register(
    "respiration", GarminService.REALTIME_RESPIRATION, Respiration, parse, encode,
)
