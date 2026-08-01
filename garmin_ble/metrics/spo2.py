"""Blood oxygen saturation.

Service code: ``REALTIME_SPO2`` (19).

Wire format (MLR payload)::

    [percent]

``255`` is the watch's "sensor not ready" sentinel and yields no reading.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..constants import GarminService
from .base import Reading, register

_SENSOR_NOT_READY = 255


@dataclass(frozen=True)
class SpO2(Reading):
    """Peripheral oxygen saturation as a percentage."""

    percent: int

    def __str__(self) -> str:
        return f"{self.percent}% SpO2"


def parse(data: bytes) -> Optional[SpO2]:
    if len(data) < 1:
        return None
    value = data[0]
    if value == _SENSOR_NOT_READY:
        return None
    return SpO2(percent=value)

def encode(reading: SpO2) -> bytes:
    """Inverse of :func:`parse`, for the simulator and round-trip tests."""
    return bytes([reading.percent])



SPO2 = register(
    "spo2", GarminService.REALTIME_SPO2, SpO2, parse, encode,
)
