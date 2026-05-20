"""SpO2 (blood oxygen saturation) parser.

Service code: ``REALTIME_SPO2`` (19).

Wire format (MLR payload)::

    [service_header, spo2]

- ``spo2`` (uint8): Oxygen saturation percentage (0-100).
- Value ``255`` is a "sensor not ready" sentinel and is ignored.
"""

from typing import Optional


def parse_spo2(data: bytes) -> Optional[int]:
    """Parse an SpO2 packet.

    Returns the SpO2 percentage, or ``None`` if the value is invalid
    (255) or the packet is too short.
    """
    if len(data) >= 2:
        spo2 = data[1]
        if spo2 != 255:
            return spo2
    return None
