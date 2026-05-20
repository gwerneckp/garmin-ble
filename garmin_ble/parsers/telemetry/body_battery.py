"""Body battery parser.

Service code: ``REALTIME_BODY_BATTERY`` (20).

Wire format (MLR payload)::

    [service_header, level]

- ``level`` (int8): Body Battery energy level (0-100).
"""

import struct
from typing import Optional


def parse_body_battery(data: bytes) -> Optional[int]:
    """Parse a body-battery packet.

    Returns the energy level, or ``None`` if the packet is too short.
    """
    if len(data) >= 2:
        level = struct.unpack('<b', bytes([data[1]]))[0]
        return level
    return None
