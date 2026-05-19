#!/usr/bin/env python3
"""Timestamp identification — track which values increment like a clock.

Shows 16-bit LE pairs with delta tracking on the first pair (suspected timestamp).
Also shows the other 6 values as both unsigned and signed 16-bit.

Usage:
  python3 examples/testing.py
"""

import asyncio
import logging
import struct

from garmin_ble import GarminClient, GarminService
from garmin_ble.logging import configure

pkt_count = 0
prev_ts = None

def on_accel_raw(data: bytes):
    global pkt_count, prev_ts
    pkt_count += 1

    if len(data) < 14:
        print(f"  SHORT: {data.hex()}")
        return

    # Parse as 16-bit LE pairs
    pairs_u16 = []
    pairs_i16 = []
    for i in range(0, 14, 2):
        pairs_u16.append(struct.unpack_from('<H', data, i)[0])
        pairs_i16.append(struct.unpack_from('<h', data, i)[0])

    ts = pairs_u16[0]
    delta = (ts - prev_ts) if prev_ts is not None else 0
    prev_ts = ts

    # Leftover byte (if present)
    tail = f"  tail=0x{data[14]:02X}" if len(data) > 14 else ""

    # Header every 20 lines
    if (pkt_count - 1) % 20 == 0:
        print()
        print(f"{'#':>5}  {'TS':>6} {'Δ':>5} │ {'P1u':>6} {'P2u':>6} {'P3u':>6} {'P4u':>6} {'P5u':>6} {'P6u':>6} │ {'P1s':>6} {'P2s':>6} {'P3s':>6} {'P4s':>6} {'P5s':>6} {'P6s':>6}")
        print("─" * 115)

    print(f"{pkt_count:5d}  {ts:6d} {delta:+5d} │ "
          f"{pairs_u16[1]:6d} {pairs_u16[2]:6d} {pairs_u16[3]:6d} {pairs_u16[4]:6d} {pairs_u16[5]:6d} {pairs_u16[6]:6d} │ "
          f"{pairs_i16[1]:+6d} {pairs_i16[2]:+6d} {pairs_i16[3]:+6d} {pairs_i16[4]:+6d} {pairs_i16[5]:+6d} {pairs_i16[6]:+6d}"
          f"{tail}")


async def main():
    configure(level=logging.WARNING)

    client = GarminClient()
    client.on("accel", on_accel_raw)

    success = await client.connect()
    if not success:
        print("Failed to connect.")
        return

    await client.register_and_start_service(GarminService.REALTIME_ACCELEROMETER)

    print("🔬 16-bit LE interpretation with timestamp delta tracking")
    print("   TS = pair[0] (suspected timestamp)")
    print("   Δ  = change from previous packet")
    print("   P1-P6 = remaining pairs (unsigned + signed)")
    print("   Ctrl+C to stop.\n")

    await client.start_sync_loop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n\nDone. {pkt_count} packets captured.")
