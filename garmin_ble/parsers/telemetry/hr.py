"""Heart-rate (HR) parser for Garmin real-time telemetry.

Service code: ``REALTIME_HR`` (6).

Wire format (MLR payload)::

    [service_header, padding?, hr, resting_hr]

- ``hr`` (uint8): Current heart rate in BPM.
- ``resting_hr`` (uint8): Resting heart rate in BPM (may be 0 if unknown).
"""

from typing import Optional, Tuple


def parse_hr(data: bytes) -> Optional[Tuple[int, int]]:
    """Parse an HR packet.

    Returns ``(hr, resting_hr)`` or ``None`` if the packet is too short.
    """
    if len(data) >= 4:
        hr = data[2]
        resting_hr = data[3]
        return hr, resting_hr
    return None
