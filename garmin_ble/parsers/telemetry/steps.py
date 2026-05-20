"""Steps parser for Garmin real-time telemetry.

Service code: ``REALTIME_STEPS`` (7).

Wire format (MLR payload)::

    [service_header, padding, steps (uint32 LE), goal (uint32 LE)]

- ``steps``: Total steps taken today.
- ``goal``: User's daily step goal.
"""

import struct
from typing import Optional, Tuple


def parse_steps(data: bytes) -> Optional[Tuple[int, int]]:
    """Parse a steps packet.

    Returns ``(steps, goal)`` or ``None`` if the packet is too short.
    """
    if len(data) >= 9:
        steps = struct.unpack('<I', data[1:5])[0]
        goal = struct.unpack('<I', data[5:9])[0]
        return steps, goal
    return None
