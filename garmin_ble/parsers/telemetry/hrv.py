"""HRV (Heart Rate Variability / RR-interval) parser.

Service code: ``REALTIME_HRV`` (12).

Wire format (MLR payload)::

    [service_header, rr_lo, rr_hi, ...]

- ``rr`` (uint16 LE): RR-interval in milliseconds.
"""

import struct
from typing import Optional


def parse_hrv(data: bytes) -> Optional[int]:
    """Parse an HRV / RR-interval packet.

    Returns the RR-interval in milliseconds, or ``None`` if the packet
    is too short.
    """
    if len(data) >= 3:
        rr = struct.unpack('<H', data[1:3])[0]
        return rr
    return None
