"""Intensity minutes parser.

Service code: ``REALTIME_INTENSITY`` (10).

Wire format (MLR payload)::

    [service_header, moderate (uint16 LE), vigorous (uint16 LE)]

- ``moderate``: Moderate-intensity activity minutes.
- ``vigorous``: Vigorous-intensity activity minutes.
"""

import struct
from typing import Optional, Tuple


def parse_intensity(data: bytes) -> Optional[Tuple[int, int]]:
    """Parse an intensity-minutes packet.

    Returns ``(moderate, vigorous)`` or ``None`` if the packet is too
    short.
    """
    if len(data) >= 5:
        moderate = struct.unpack('<H', data[1:3])[0]
        vigorous = struct.unpack('<H', data[3:5])[0]
        return moderate, vigorous
    return None
