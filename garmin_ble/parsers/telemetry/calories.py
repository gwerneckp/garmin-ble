"""Calories parser.

Service code: ``REALTIME_CALORIES`` (8).

Wire format (MLR payload)::

    [service_header, total (uint32 LE), active (uint32 LE)]

- ``total``: Total calories burned today.
- ``active``: Active (exercise) calories burned today.
"""

import struct
from typing import Optional, Tuple


def parse_calories(data: bytes) -> Optional[Tuple[int, int]]:
    """Parse a calories packet.

    Returns ``(total_cal, active_cal)`` or ``None`` if the packet is
    too short.
    """
    if len(data) >= 9:
        total = struct.unpack('<I', data[1:5])[0]
        active = struct.unpack('<I', data[5:9])[0]
        return total, active
    return None
