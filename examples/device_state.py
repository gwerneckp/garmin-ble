#!/usr/bin/env python3
"""Query device state over Protobuf, and answer what the watch asks of you.

Usage:
    python examples/device_state.py
    python examples/device_state.py --simulate

Both directions of the protobuf channel:
  * we ask the watch for its battery level, and await the answer
  * the watch asks us for the phone's battery level, and we answer it
"""

import argparse
import asyncio
import logging

from garmin_ble import GarminBleError, RequestTimeout, Watch
from garmin_ble.logging import configure
from garmin_ble.protobuf import gdi_device_status_pb2

DeviceStatus = gdi_device_status_pb2.DeviceStatusService


async def main(simulate: bool) -> None:
    configure(level=logging.INFO)

    session = Watch.simulated() if simulate else Watch.discover()

    async with session as watch:
        print(f"✅ Connected to {watch.info.name}\n")

        # ── The watch asking us ──────────────────────────────────────────────
        #
        # Dispatch is on the concrete message type. Return the bare response and
        # the library wraps it in the Smart envelope, frames it, and matches it
        # to the incoming request id.
        @watch.responds_to(DeviceStatus.RemoteDeviceBatteryStatusRequest)
        def _(_request):
            print("📱 Watch asked for the phone's battery level — replying 99%")
            return DeviceStatus.RemoteDeviceBatteryStatusResponse(
                status=DeviceStatus.ResponseStatus.OK,
                current_battery_level=99,
            )

        # ── Us asking the watch ──────────────────────────────────────────────
        #
        # `watch.battery()` is the typed convenience wrapper.
        print("📡 Requesting battery status...")
        battery = await watch.battery()
        print(f"🔋 Watch battery level: {battery.percent}% ({battery.status})\n")

        # The same query at the raw protobuf level, for anything without a
        # wrapper. `request()` correlates the response by request id, so several
        # can be in flight at once.
        response = await watch.request(DeviceStatus.RemoteDeviceBatteryStatusRequest())
        print(f"   (raw protobuf said: {response.current_battery_level}%)\n")

        # Which protobuf services a watch answers varies by model and firmware.
        try:
            for app in await watch.installed_apps(timeout=5):
                print(f"📱 {app}")
        except RequestTimeout:
            print("· installed_apps: not answered by this watch")

        print("\nListening for messages from the watch. Ctrl+C to stop.")
        await asyncio.Event().wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulate", action="store_true", help="use an in-process watch")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.simulate))
    except KeyboardInterrupt:
        print("\nDisconnecting...")
    except GarminBleError as exc:
        print(f"\n❌ {exc}")
