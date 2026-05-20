"""Stress level parser.

Service code: ``REALTIME_STRESS`` (13).

Wire format (MLR payload)::

    [service_header, level]

- ``level`` (int8): Stress level (0-100, where 0 = low, 100 = high).
"""

import struct
from typing import Optional


def parse_stress(data: bytes) -> Optional[int]:
    """Parse a stress-level packet.

    Returns the stress level, or ``None`` if the packet is too short.
    """
    if len(data) >= 2:
        level = struct.unpack('<b', bytes([data[1]]))[0]
        return level
    return None
