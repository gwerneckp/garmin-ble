"""Respiration rate parser.

Service code: ``REALTIME_RESPIRATION`` (21).

Wire format (MLR payload)::

    [service_header, breaths_per_min]

- ``breaths_per_min`` (int8): Respiratory rate in breaths/minute (1-127).
- Value ``0`` is treated as invalid (sensor not ready).
"""

import struct
from typing import Optional


def parse_respiration(data: bytes) -> Optional[int]:
    """Parse a respiration packet.

    Returns breaths per minute, or ``None`` if the value is ``<= 0``
    or the packet is too short.
    """
    if len(data) >= 2:
        breaths = struct.unpack('<b', bytes([data[1]]))[0]
        if breaths > 0:
            return breaths
    return None
