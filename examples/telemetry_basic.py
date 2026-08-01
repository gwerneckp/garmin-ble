#!/usr/bin/env python3
"""Live telemetry in about ten lines.

Usage:
    python examples/telemetry_basic.py
    python examples/telemetry_basic.py --simulate   # no hardware needed
"""

import argparse
import asyncio
import logging

from garmin_ble import GarminBleError, Watch, metrics
from garmin_ble.logging import configure


async def main(simulate: bool) -> None:
    configure(level=logging.INFO)

    session = Watch.simulated() if simulate else Watch.discover()

    # Opening the session connects, completes the handshake, and starts the
    # keep-alive heartbeat. Leaving the block always disconnects.
    async with session as watch:
        print(f"Connected to {watch.info.name}. Ctrl+C to stop.\n")

        # Registering a handler subscribes to the metric, which registers and
        # starts its service on the watch. There is no second list of service
        # codes to keep in sync with these.
        @watch.on(metrics.HEART_RATE)
        def _(reading: metrics.HeartRate) -> None:
            print(f"❤️  {reading.bpm} BPM (resting {reading.resting_bpm})")

        @watch.on(metrics.STEPS)
        def _(reading: metrics.Steps) -> None:
            print(f"👣 {reading.count:,} / {reading.goal:,} steps ({reading.fraction_of_goal:.0%})")

        @watch.on(metrics.HRV)
        def _(reading: metrics.Hrv) -> None:
            print(f"💓 {reading.rr_ms} ms RR-interval")

        # Nothing to poll and no sync loop to start: the session stays alive on
        # its own until you leave the block.
        await asyncio.Event().wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulate", action="store_true", help="use an in-process watch")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.simulate))
    except KeyboardInterrupt:
        print("\nExiting...")
    except GarminBleError as exc:
        print(f"\n{exc}")
