#!/usr/bin/env python3
"""Raw accelerometer data stream — record & label sessions.

Known:
  - Bytes 0-1: 16-bit LE unsigned timestamp (ms), ~115ms between packets
  - Bytes 2-14: UNKNOWN (13 bytes, no assumptions)

Flow:
  1. Connects to the watch and starts streaming
  2. Press ENTER to start a 10-second recording
  3. After recording, type a label (e.g. "shelf_faceup", "floor_tilted")
  4. Data is saved to rev_eng/<label>_<timestamp>.json
  5. Loop — press ENTER again for another recording, Ctrl+C to quit

Usage:
  python3 examples/reverse_eng.py
"""

import asyncio
import json
import logging
import os
import struct
import sys
import time

from garmin_ble import GarminClient, GarminService
from garmin_ble.logging import configure

RECORD_DURATION = 10  # seconds

# ── State ─────────────────────────────────────────────────────────────────

recording = False
buffer = []
live_count = 0
prev_ts = None


def parse_packet(data: bytes):
    """Parse with only confirmed knowledge: bytes 0-1 = timestamp."""
    n = len(data)
    ts = struct.unpack_from('<H', data, 0)[0] if n >= 2 else 0
    rest = list(data[2:]) if n > 2 else []

    return {
        "capture_time": time.time(),
        "len": n,
        "hex": data.hex(),
        "timestamp": ts,
        "rest_bytes": rest,         # raw individual bytes, no assumptions
        "raw_bytes": list(data),    # full packet for future re-parsing
    }


def on_accel_raw(data: bytes):
    global live_count, prev_ts
    live_count += 1

    if recording:
        buffer.append(parse_packet(data))

    if not recording:
        return

    if len(data) < 2:
        sys.stdout.write(f"\r  📡 pkt {live_count:6d}  SHORT")
        sys.stdout.flush()
        return

    ts = struct.unpack_from('<H', data, 0)[0]
    delta = (ts - prev_ts) if prev_ts is not None else 0
    prev_ts = ts
    rest = data[2:]

    # Live display: timestamp + each remaining byte individually
    rest_str = " ".join(f"{b:3d}" for b in rest)
    sys.stdout.write(f"\r  📡 #{live_count:5d}  ts={ts:5d} Δ{delta:+5d}  │ {rest_str}")
    sys.stdout.flush()


def save_session(filename: str):
    """Save buffer to examples/data/<filename>.json"""
    save_dir = os.path.join(os.path.dirname(__file__), "data")
    os.makedirs(save_dir, exist_ok=True)

    if not filename.endswith(".json"):
        filename += ".json"
    filepath = os.path.join(save_dir, filename)

    session = {
        "recorded_at": time.strftime("%Y%m%d_%H%M%S"),
        "duration_s": RECORD_DURATION,
        "packet_count": len(buffer),
        "packets": buffer,
    }

    with open(filepath, "w") as f:
        json.dump(session, f, indent=2)

    print(f"\n  💾 Saved {len(buffer)} packets → {filepath}")
    print(f"     File size: {os.path.getsize(filepath) / 1024:.1f} KB")


async def input_async(prompt: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: input(prompt))


async def main():
    global recording, buffer

    configure(level=logging.WARNING)

    client = GarminClient()
    client.on("accel", on_accel_raw)

    success = await client.connect()
    if not success:
        print("Failed to connect.")
        return

    await client.register_and_start_service(GarminService.REALTIME_ACCELEROMETER)
    sync_task = asyncio.create_task(client.start_sync_loop())

    print("🔬 Raw accelerometer stream active.")
    print("   KNOWN: bytes[0:2] = 16-bit LE timestamp (ms)")
    print("   REST:  bytes[2:] = raw individual bytes (no assumptions)")
    print(f"   Press ENTER to record {RECORD_DURATION}s, Ctrl+C to quit.\n")

    try:
        while True:
            await input_async("  ⏎  Press ENTER to start recording... ")

            buffer = []
            recording = True
            print(f"\n  🔴 RECORDING for {RECORD_DURATION}s...")

            await asyncio.sleep(RECORD_DURATION)

            recording = False
            print(f"\n  ⏹  Stopped. Captured {len(buffer)} packets.\n")

            if len(buffer) == 0:
                print("  ⚠️  No packets captured. Is the watch streaming?")
                continue

            fname = await input_async("  📁 Filename: ")
            fname = fname.strip()
            if not fname:
                fname = f"recording_{int(time.time())}"

            save_session(fname)
            print()

    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        sync_task.cancel()
        await client.disconnect()
        print(f"\nDone. {live_count} total packets seen.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting...")
