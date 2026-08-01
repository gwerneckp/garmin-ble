#!/usr/bin/env python3
"""Every telemetry stream at once, plus link events and live diagnostics.

Usage:
    python examples/telemetry_advanced.py
    python examples/telemetry_advanced.py --simulate

Shows:
  * one merged stream instead of one callback per metric
  * automatic reconnection with subscription restore
  * typed link events (drops, time sync, service registration)
  * a periodic diagnostics snapshot
"""

import argparse
import asyncio
import logging
from datetime import timedelta

from garmin_ble import GarminBleError, ServiceUnavailable, Watch, events
from garmin_ble.logging import configure


async def show_events(watch: Watch) -> None:
    """Print link and protocol events as they happen."""
    async for event in watch.events():
        if isinstance(event, events.Disconnected):
            print(f"\n⚠️  link dropped: {event.reason} — reconnecting…")
        elif isinstance(event, events.Reconnected):
            print(f"✅ back after {event.attempts} attempt(s), "
                  f"{event.restored} subscription(s) restored\n")
        elif isinstance(event, events.DeviceIdentified):
            print(f"ℹ️  {event}")
        elif isinstance(event, events.ServiceRegistered):
            print(f"🔗 {event}")


async def show_diagnostics(watch: Watch, every: float = 15.0) -> None:
    """Print a protocol snapshot periodically."""
    while True:
        await asyncio.sleep(every)
        print(f"\n{'─' * 58}")
        for line in watch.diagnostics.summary().splitlines():
            print(f"  {line}")
        print(f"{'─' * 58}\n")


async def main(simulate: bool) -> None:
    configure(level=logging.INFO)

    session = (
        Watch.simulated()
        if simulate
        else Watch.discover(heartbeat=timedelta(seconds=60), reconnect="exponential")
    )

    async with session as watch:
        print("🚀 Garmin Advanced Feature Showcase")
        print("   - One merged telemetry stream")
        print("   - Automatic reconnection with subscription restore")
        print("   - Typed link events")
        print("   - Live diagnostics")
        print("-" * 58)
        print(f"\nConnected to {watch.info.name}. Ctrl+C to stop.\n")

        background = [
            asyncio.create_task(show_events(watch)),
            asyncio.create_task(show_diagnostics(watch)),
        ]

        try:
            # `stream_all()` with no arguments takes everything this watch
            # supports and interleaves it in arrival order. A metric the watch
            # refuses is skipped with a note rather than aborting the run —
            # which is why ServiceUnavailable is worth catching by name.
            async for reading in watch.stream_all():
                print(f"  {reading.metric.name:<14} {reading}")
        except ServiceUnavailable as exc:
            print(f"⚠️  {exc}")
        finally:
            for task in background:
                task.cancel()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulate", action="store_true", help="use an in-process watch")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.simulate))
    except KeyboardInterrupt:
        print("\nStopping showcase...")
    except GarminBleError as exc:
        print(f"\n{exc}")
