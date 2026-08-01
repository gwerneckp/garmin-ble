"""Raw accelerometer samples.

Service code: ``REALTIME_ACCELEROMETER`` (16).

Wire format (MLR payload)::

    [timestamp (uint16 LE), 14-byte packed sample block]

The sample block holds nine signed 12-bit values — three ``(x, y, z)`` triples
— packed two-per-three-bytes::

    byte 0        byte 1        byte 2
    [ v0 lo 8 ]  [v1 lo 4|v0 hi 4]  [ v1 hi 8 ]

Four such groups give eight values; the ninth is read from the final
byte-and-a-half. Scale is 256 counts per g.

Both representations are kept: ``sample.counts`` is what the watch actually
sent, ``sample.g`` is the physical value. Keeping the scale here means no caller
has to remember it.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Iterator, Optional, Tuple

from ..constants import GarminService
from .base import Reading, register

#: Accelerometer counts per g of acceleration.
COUNTS_PER_G = 256.0

_TIMESTAMP_LEN = 2
_BLOCK_LEN = 14
_PACKET_LEN = _TIMESTAMP_LEN + _BLOCK_LEN


@dataclass(frozen=True)
class AccelSample:
    """One three-axis acceleration sample."""

    x: int
    y: int
    z: int

    @property
    def counts(self) -> Tuple[int, int, int]:
        """Raw signed 12-bit counts as sent by the watch."""
        return (self.x, self.y, self.z)

    @property
    def g(self) -> Tuple[float, float, float]:
        """Acceleration per axis in g."""
        return (self.x / COUNTS_PER_G, self.y / COUNTS_PER_G, self.z / COUNTS_PER_G)

    @property
    def magnitude_g(self) -> float:
        """Total acceleration magnitude in g. Reads ~1.0 when stationary."""
        gx, gy, gz = self.g
        return math.sqrt(gx * gx + gy * gy + gz * gz)

    def __str__(self) -> str:
        gx, gy, gz = self.g
        return f"({gx:+.3f}g, {gy:+.3f}g, {gz:+.3f}g)"


@dataclass(frozen=True)
class AccelPacket(Reading):
    """A burst of three accelerometer samples sharing one timestamp.

    Iterating the packet iterates its samples, so ``for s in packet:`` works.
    """

    samples: Tuple[AccelSample, ...]
    timestamp_ms: int = 0

    def __iter__(self) -> Iterator[AccelSample]:
        return iter(self.samples)

    def __len__(self) -> int:
        return len(self.samples)

    def __str__(self) -> str:
        first = self.samples[0] if self.samples else "-"
        return f"{len(self.samples)} samples, first {first}"


def _unpack_12bit(block: bytes) -> Tuple[int, ...]:
    """Expand the 14-byte packed block into nine signed 12-bit values."""
    values = []
    for i in range(4):
        b0, b1, b2 = block[3 * i], block[3 * i + 1], block[3 * i + 2]
        values.append(b0 | ((b1 & 0x0F) << 8))
        values.append((b1 >> 4) | (b2 << 4))
    b0, b1 = block[12], block[13]
    values.append(b0 | ((b1 & 0x0F) << 8))
    # 12-bit two's complement
    return tuple(v - 4096 if v >= 2048 else v for v in values)


def parse(data: bytes) -> Optional[AccelPacket]:
    if len(data) < _PACKET_LEN:
        return None
    timestamp_ms = struct.unpack("<H", data[:_TIMESTAMP_LEN])[0]
    v = _unpack_12bit(data[_TIMESTAMP_LEN:_PACKET_LEN])
    samples = tuple(AccelSample(v[i], v[i + 1], v[i + 2]) for i in (0, 3, 6))
    return AccelPacket(samples=samples, timestamp_ms=timestamp_ms)


def _pack_12bit(values: "Tuple[int, ...]") -> bytes:
    """Inverse of :func:`_unpack_12bit`."""
    raw = [v + 4096 if v < 0 else v for v in values]
    out = bytearray()
    for i in range(4):
        even, odd = raw[2 * i], raw[2 * i + 1]
        out.append(even & 0xFF)
        out.append(((even >> 8) & 0x0F) | ((odd & 0x0F) << 4))
        out.append((odd >> 4) & 0xFF)
    out.append(raw[8] & 0xFF)
    out.append((raw[8] >> 8) & 0x0F)
    return bytes(out)


def encode(reading: AccelPacket) -> bytes:
    """Inverse of :func:`parse`, for the simulator and round-trip tests."""
    values = []
    for sample in reading.samples:
        values.extend((sample.x, sample.y, sample.z))
    values += [0] * (9 - len(values))
    timestamp = struct.pack("<H", reading.timestamp_ms & 0xFFFF)
    return timestamp + _pack_12bit(tuple(values[:9]))


ACCELEROMETER = register(
    "accelerometer", GarminService.REALTIME_ACCELEROMETER, AccelPacket, parse, encode,
    "Three-axis accelerometer bursts at 256 counts per g",
)
