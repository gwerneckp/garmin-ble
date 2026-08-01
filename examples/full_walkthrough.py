#!/usr/bin/env python3
"""Full Walkthrough — Verify every feature of garmin-ble in one shot.

Usage:
    python examples/full_walkthrough.py
    python examples/full_walkthrough.py --simulate   # no hardware needed

What it does:
  1. Connect to your Garmin watch
  2. Register ALL telemetry services (HR, steps, HRV, SpO2, respiration,
     calories, intensity, stress, body battery, accelerometer)
  3. Wait for at least one data point from each service
  4. Ask the watch for its battery level via Protobuf
  5. Print a final summary with connection stats
  6. Disconnect cleanly

Run this if you want a quick "does everything work?" verification.
You need your watch nearby and NOT connected to a phone via BLE — or pass
``--simulate`` to run the same checks against an in-process watch.

For a guided tour of the API itself, see ``full_walkthrough_idealistic.py``.
"""

import argparse
import asyncio
import time
from datetime import timedelta

from garmin_ble import Checklist, GarminBleError, Metric, Watch, events
from garmin_ble.protobuf import gdi_device_status_pb2

DeviceStatus = gdi_device_status_pb2.DeviceStatusService

CHECKED = "✅"
WAITING = "⏳"
SKIPPED = "⚠️"

#: System-level things to confirm, beyond the telemetry itself.
SYSTEM_CHECKS = [
    ("connected", "BLE connected"),
    ("handshake", "GFDI handshake (CLOSE_ALL + REGISTER_ML)"),
    ("heartbeat", "Heartbeat enabled"),
    ("protobuf_rx", "Protobuf message received"),
    ("battery_rx", "Battery level response"),
    ("system_event", "System event / time sync from watch"),
]


class Progress:
    """Tracks the system checks and prints each one the first time it passes."""

    def __init__(self) -> None:
        self.checks = {}
        self.started = time.monotonic()

    def passed(self, name: str, detail: str = "") -> None:
        if name in self.checks:
            return
        self.checks[name] = True
        elapsed = time.monotonic() - self.started
        suffix = f"  {detail}" if detail else ""
        print(f"  {CHECKED} {name:<16s} ({elapsed:5.1f}s){suffix}")

    @property
    def all_passed(self) -> bool:
        return all(self.checks.get(key) for key, _ in SYSTEM_CHECKS)

    def checklist(self) -> str:
        lines = []
        for key, label in SYSTEM_CHECKS:
            mark = CHECKED if self.checks.get(key) else WAITING
            lines.append(f"    {mark}  {label}")
        return "\n".join(lines)


async def watch_for_events(watch: Watch, progress: Progress) -> None:
    """Mark system checks as the corresponding events arrive."""
    async for event in watch.events():
        if isinstance(event, (events.TimeSyncRequested, events.SystemEvent)):
            progress.passed("system_event")
        elif isinstance(event, events.ProtobufReceived):
            progress.passed("protobuf_rx")
        elif isinstance(event, events.Disconnected):
            print(f"\n  {SKIPPED} link dropped: {event.reason}")


def open_watch(simulate: bool):
    if simulate:
        return Watch.simulated(profile="fenix7", heartbeat=timedelta(seconds=5))
    return Watch.discover(
        timeout=timedelta(seconds=30),
        heartbeat=timedelta(seconds=60),
        reconnect="exponential",
    )


async def main(simulate: bool) -> None:
    progress = Progress()

    print("=" * 58)
    print("  Garmin BLE — Full Feature Walkthrough")
    print("=" * 58)
    print(f"\n  {WAITING} Scanning for a Garmin watch...")

    # Connecting, the handshake, and the keep-alive heartbeat are all part of
    # opening the session — and leaving the block always disconnects, even on
    # Ctrl+C or an exception.
    try:
        session = open_watch(simulate)
        watch = await session.open()
    except GarminBleError as exc:
        print(f"\n  ❌ {exc}")
        print("     Make sure the watch is nearby and NOT connected to your phone.")
        print("     (or re-run with --simulate to check the library itself)")
        return

    try:
        progress.passed("connected")
        progress.passed("handshake")
        progress.passed("heartbeat")
        await asyncio.sleep(0.5)  # let DEVICE_INFORMATION land, if the watch sends one
        print(f"     Device:    {watch.info.name} ({watch.info.model}, fw {watch.info.firmware})")
        print(f"     Address:   {watch.info.address}")
        print(f"     MTU:       {watch.info.mtu}")

        event_task = asyncio.create_task(watch_for_events(watch, progress))

        # ── Answer the watch if it asks for our battery level ──
        @watch.responds_to(DeviceStatus.RemoteDeviceBatteryStatusRequest)
        def _(_request):
            print("\n  📱 Watch asked for phone battery — replying with 99%")
            return DeviceStatus.RemoteDeviceBatteryStatusResponse(current_battery_level=99)

        # ── Collect one sample from every telemetry service ──
        #
        # Subscribing to a metric registers and starts its service, so there is
        # no second list of service codes to keep in sync.
        print(f"\n{'─' * 58}")
        print(f"  {WAITING} Getting telemetry + system events…")
        print("  Keep the watch nearby. Runs until everything arrives or you Ctrl+C.\n")

        result = await watch.collect(
            Metric.ALL_TELEMETRY,
            until="first_sample_each",
            timeout=timedelta(seconds=60),
        )

        if result.complete:
            print(f"\n  {CHECKED} All {len(Metric.ALL_TELEMETRY)} telemetry types received!")
        else:
            missing = ", ".join(m.name for m in result.missing)
            print(f"\n  {WAITING} Timed out waiting for: {missing}")
            print("     Moving on… some services may need movement to trigger.\n")

        # ── Ask for the battery level over Protobuf ──
        print(f"\n  {WAITING} Sending Protobuf battery request...")
        try:
            battery = await watch.battery(timeout=timedelta(seconds=10))
            progress.passed("battery_rx", f"{battery.percent}%")
        except GarminBleError as exc:
            print(f"  {SKIPPED} battery request failed: {exc}")

        # ── Wait out the remaining system checks ──
        if not progress.all_passed:
            print(f"\n  {WAITING} Still waiting for system events...")
            print("     (time sync, protobuf messages, etc.)\n")

            deadline = time.monotonic() + 120.0
            while time.monotonic() < deadline and not progress.all_passed:
                remaining = int(deadline - time.monotonic())
                if remaining > 0 and remaining % 15 == 0:
                    pending = [l for k, l in SYSTEM_CHECKS if not progress.checks.get(k)]
                    print(f"     ⏳ still waiting ({remaining}s left): {', '.join(pending)}")
                await asyncio.sleep(1)

        event_task.cancel()

        # ── Summary ──
        total = time.monotonic() - progress.started
        print(f"\n{'=' * 58}")
        print("  WALKTHROUGH SUMMARY")
        print(f"{'=' * 58}")
        print(f"\n  Duration: {total:.1f}s\n")
        print("  Telemetry:")
        print(Checklist.from_collection(result))
        print("\n  System:")
        print(progress.checklist())

        if result.complete and progress.all_passed:
            print(f"\n  {CHECKED} Everything works! All features verified successfully.")
        elif result.complete:
            print(f"\n  {CHECKED} All telemetry OK but some system checks didn't fire.")
            print("     The watch may not send some events spontaneously during this session.")
        else:
            print(f"\n  {WAITING} Partial success — some features didn't fire.")
            print("     This is normal for services that need movement / time.")
            print("     Run the watch for longer or move around to trigger them.")

        print("\n  Connection stats:")
        for line in watch.diagnostics.summary().splitlines():
            print(f"    {line}")

    finally:
        print("\n  👋 Disconnecting...")
        await session.aclose()
        print("  ✅ Done.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify every garmin-ble feature.")
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="run against an in-process simulated watch instead of real hardware",
    )
    args = parser.parse_args()
    try:
        asyncio.run(main(args.simulate))
    except KeyboardInterrupt:
        print("\n\n  Interrupted by user.")
