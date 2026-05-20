"""Accelerometer parser.

Service code: ``REALTIME_ACCELEROMETER`` (16).

Wire format (MLR payload)::

    [0x10, ts_lo, ts_hi, 12-byte sample-block, ...]

The 12-bit packed format stores 9 values (3 × (X, Y, Z)) across 14 bytes::

    Byte 3-14: 4 groups of 3-byte triples, each encoding two 12-bit values.

Scale: 1 g = 256 LSB.  Values are signed 12-bit (range -2048…2047).
"""

from typing import List, Optional, Tuple


def parse_accel(data: bytes) -> Optional[List[Tuple[float, float, float]]]:
    """Parse an accelerometer packet.

    Returns a list of ``(x, y, z)`` tuples in g-units, or ``None`` if
    the packet is too short or no callback is registered.

    The caller should check whether the returned list is empty.
    """
    if len(data) < 17:
        return None

    # data[0] is the service header (0x10)
    # data[1:3] is the 16-bit timestamp (ignored)
    payload = data[3:17]

    vals = []
    for i in range(4):
        b0, b1, b2 = payload[3 * i], payload[3 * i + 1], payload[3 * i + 2]
        v_even = b0 | ((b1 & 0x0F) << 8)
        v_odd = (b1 >> 4) | (b2 << 4)
        vals.extend([v_even, v_odd])

    b0, b1 = payload[12], payload[13]
    v8 = b0 | ((b1 & 0x0F) << 8)
    vals.append(v8)

    # 12-bit signed -> float (1g = 256 LSB)
    g_vals = [(v if v < 2048 else v - 4096) / 256.0 for v in vals]

    samples = [
        (g_vals[0], g_vals[1], g_vals[2]),
        (g_vals[3], g_vals[4], g_vals[5]),
        (g_vals[6], g_vals[7], g_vals[8]),
    ]

    return samples
