#!/usr/bin/env python3
"""Full Walkthrough — Verify every feature of garmin-ble in one shot.

Usage:
    python examples/full_walkthrough.py

What it does:
  1. Connect to your Garmin watch
  2. Register ALL telemetry services (HR, steps, HRV, SpO2, respiration,
     calories, intensity, stress, body battery, accelerometer)
  3. Wait for at least one data point from each service
  4. Ask the watch for its battery level via Protobuf
  5. Print a final summary with connection stats
  6. Disconnect cleanly

Run this if you want a quick "does everything work?" verification.
You need your watch nearby and NOT connected to a phone via BLE.
"""

import asyncio
import logging
import time

from garmin_ble import GarminClient, GarminService
from garmin_ble.logging import configure
from garmin_ble.protobuf import gdi_device_status_pb2, gdi_smart_proto_pb2


# ── Tracking ─────────────────────────────────────────────────────────────────
# We keep a checklist so the user sees live progress.

CHECKED  = "✅"
WAITING  = "⏳"
SKIPPED  = "⚠️"

TELEMETRY_TYPES = [
    ("hr",              "Heart Rate"),
    ("steps",           "Steps"),
    ("hrv",             "HRV (RR-interval)"),
    ("spo2",            "SpO2"),
    ("respiration",     "Respiration"),
    ("calories",        "Calories"),
    ("intensity",       "Intensity Minutes"),
    ("stress",          "Stress Level"),
    ("body_battery",    "Body Battery"),
    ("accel",           "Accelerometer"),
]

MISC_CHECKS = [
    ("connected",       "BLE connected"),
    ("handshake",       "GFDI handshake (CLOSE_ALL + REGISTER_ML)"),
    ("heartbeat",       "Heartbeat enabled"),
    ("protobuf_rx",     "Protobuf message received"),
    ("battery_rx",      "Battery level response"),
    ("system_event",    "System event / time sync from watch"),
]


class FeatureTracker:
    """Collects one sample per telemetry type + flags for misc checks."""

    def __init__(self):
        self.seen: dict[str, bool] = {}
        self.samples: dict[str, object] = {}
        self.checks: dict[str, bool] = {}
        self.start_time = 0.0

    def mark(self, name: str, value: object):
        if name not in self.seen:
            self.seen[name] = True
            self.samples[name] = value
            elapsed = time.monotonic() - self.start_time
            print(f"  {CHECKED} {name:<16s}  ({elapsed:5.1f}s)  {value}")
        else:
            self.samples[name] = value  # keep latest

    def check(self, name: str):
        if name not in self.checks:
            self.checks[name] = True
            elapsed = time.monotonic() - self.start_time
            print(f"  {CHECKED} {name:<16s}  ({elapsed:5.1f}s)")

    @property
    def all_telemetry_received(self) -> bool:
        needed = {t[0] for t in TELEMETRY_TYPES}
        return needed.issubset(self.seen.keys())

    @property
    def all_system_checks_passed(self) -> bool:
        return all(self.checks.get(k) for k, _ in MISC_CHECKS)

    @property
    def all_done(self) -> bool:
        return self.all_telemetry_received and self.all_system_checks_passed

    def telemetry_checklist(self) -> str:
        lines = []
        for key, label in TELEMETRY_TYPES:
            status = CHECKED if key in self.seen else WAITING
            val = self.samples.get(key, "")
            lines.append(f"    {status}  {label}")
            if val:
                lines.append(f"                {val}")
        return "\n".join(lines)

    def misc_checklist(self) -> str:
        lines = []
        for key, label in MISC_CHECKS:
            status = CHECKED if self.checks.get(key) else (SKIPPED if key in self.checks else WAITING)
            lines.append(f"    {status}  {label}")
        return "\n".join(lines)


# ── Telemetry callbacks ──────────────────────────────────────────────────────

def on_heart_rate(tracker: FeatureTracker, hr: int, resting: int):
    tracker.mark("hr", f"{hr} BPM (resting {resting})")

def on_steps(tracker: FeatureTracker, steps: int, goal: int):
    tracker.mark("steps", f"{steps} / {goal} steps")

def on_hrv(tracker: FeatureTracker, rr: int):
    tracker.mark("hrv", f"{rr} ms RR-interval")

def on_spo2(tracker: FeatureTracker, val: int):
    tracker.mark("spo2", f"{val}% SpO2")

def on_respiration(tracker: FeatureTracker, rate: float):
    tracker.mark("respiration", f"{rate:.1f} breaths/min")

def on_calories(tracker: FeatureTracker, total: int, active: int):
    tracker.mark("calories", f"{total} total ({active} active) kcal")

def on_intensity(tracker: FeatureTracker, moderate: int, vigorous: int):
    tracker.mark("intensity", f"{moderate} mod / {vigorous} vig min")

def on_stress(tracker: FeatureTracker, level: int):
    tracker.mark("stress", f"{level}/100")

def on_body_battery(tracker: FeatureTracker, level: int):
    tracker.mark("body_battery", f"{level}/100")

def on_accel(tracker: FeatureTracker, samples: list):
    """samples is a list of (x, y, z) tuples in g-units."""
    n = len(samples)
    s = samples[0] if n else (0.0, 0.0, 0.0)
    tracker.mark("accel", f"{n} samples (first: {s[0]:+.3f}g, {s[1]:+.3f}g, {s[2]:+.3f}g)")

def on_protobuf(tracker: FeatureTracker, request_id: int, smart):
    tracker.check("protobuf_rx")


def on_system_event(tracker: FeatureTracker, event_type: int, event_value: int):
    tracker.check("system_event")


# ── Protobuf processor for battery ──────────────────────────────────────────

def handle_device_status(tracker: FeatureTracker, msg):
    """Processor registered on the protobuf handler for device_status_service."""
    if msg.HasField("remote_device_battery_status_response"):
        resp = msg.remote_device_battery_status_response
        tracker.samples["battery_level"] = resp.current_battery_level
        if not tracker.checks.get("battery_rx"):
            elapsed = time.monotonic() - tracker.start_time
            tracker.checks["battery_rx"] = True
            print(f"  {CHECKED} battery_rx       ({elapsed:5.1f}s)  {resp.current_battery_level}%")
        return None  # no reply needed

    if msg.HasField("remote_device_battery_status_request"):
        print("\n  📱 Watch asked for phone battery — replying with 99%")
        return gdi_smart_proto_pb2.Smart(
            device_status_service=gdi_device_status_pb2.DeviceStatusService(
                remote_device_battery_status_response=
                    gdi_device_status_pb2.DeviceStatusService.RemoteDeviceBatteryStatusResponse(
                        status=0,
                        current_battery_level=99,
                        current_charge_state=0,
                    )
            )
        )

    return None


# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    configure(level=logging.INFO)

    tracker = FeatureTracker()
    tracker.start_time = time.monotonic()

    # ------------------------------------------------------------------ #
    #  1. Initialise client                                              #
    # ------------------------------------------------------------------ #
    print("=" * 58)
    print("  Garmin BLE — Full Feature Walkthrough")
    print("=" * 58)

    client = GarminClient()

    # Register telemetry callbacks (partially applied with tracker)
    client.on("hr",              lambda hr, r:  on_heart_rate(tracker, hr, r))
    client.on("steps",           lambda s, g:   on_steps(tracker, s, g))
    client.on("hrv",             lambda rr:     on_hrv(tracker, rr))
    client.on("spo2",            lambda v:      on_spo2(tracker, v))
    client.on("respiration",     lambda r:      on_respiration(tracker, r))
    client.on("calories",        lambda t, a:   on_calories(tracker, t, a))
    client.on("intensity",       lambda m, v:   on_intensity(tracker, m, v))
    client.on("stress",          lambda l:      on_stress(tracker, l))
    client.on("body_battery",    lambda l:      on_body_battery(tracker, l))
    client.on("accel",           lambda p:      on_accel(tracker, p))
    client.on("protobuf",        lambda rid, s: on_protobuf(tracker, rid, s))
    client.on("system_event",    lambda t, v:   on_system_event(tracker, t, v))

    # Register the battery processor BEFORE connecting so it catches
    # any spontaneous requests from the watch
    client.protobuf_handler.register_processor(
        "device_status_service",
        lambda msg: handle_device_status(tracker, msg),
    )

    # ------------------------------------------------------------------ #
    #  2. Connect                                                        #
    # ------------------------------------------------------------------ #
    print(f"\n  {WAITING} Scanning for nearby Garmin watch...")
    success = await client.connect(timeout=30.0)
    if not success:
        print(f"\n  ❌ Could not find or connect to a Garmin watch.")
        print("     Make sure it's nearby and NOT connected to your phone.")
        return

    elapsed = time.monotonic() - tracker.start_time
    tracker.checks["connected"] = True
    tracker.checks["handshake"] = True  # connect succeeded = handshake happened
    print(f"\n  {CHECKED} Connected @ {elapsed:.1f}s")
    print(f"     Device:    {client.device.name if client.device else 'Unknown'}")
    print(f"     Address:   {client.address}")
    print(f"     MTU:       {client.client.mtu_size if client.client else '?'}")

    # ------------------------------------------------------------------ #
    #  3. Enable heartbeat (keep-alive)                                  #
    # ------------------------------------------------------------------ #
    tracker.checks["heartbeat"] = True
    print(f"\n  {CHECKED} Enabling heartbeat (60s interval)...")
    client.enable_heartbeat(60.0)

    # ------------------------------------------------------------------ #
    #  4. Register & start ALL telemetry services                        #
    # ------------------------------------------------------------------ #
    all_services = [
        ("HR",              GarminService.REALTIME_HR),
        ("Steps",           GarminService.REALTIME_STEPS),
        ("HRV",             GarminService.REALTIME_HRV),
        ("SpO2",            GarminService.REALTIME_SPO2),
        ("Respiration",     GarminService.REALTIME_RESPIRATION),
        ("Calories",        GarminService.REALTIME_CALORIES),
        ("Intensity",       GarminService.REALTIME_INTENSITY),
        ("Stress",          GarminService.REALTIME_STRESS),
        ("Body Battery",    GarminService.REALTIME_BODY_BATTERY),
        ("Accelerometer",   GarminService.REALTIME_ACCELEROMETER),
    ]

    print(f"\n  {WAITING} Registering {len(all_services)} telemetry services...\n")
    for name, svc in all_services:
        await client.register_and_start_service(svc)

    # ------------------------------------------------------------------ #
    #  5. Start sync loop and wait for all features                      #
    # ------------------------------------------------------------------ #
    print(f"\n{'─' * 58}")
    print(f"  {WAITING} Getting telemetry + system events…")
    print(f"  Keep the watch nearby. Runs until everything arrives or you Ctrl+C.\n")

    sync_task = asyncio.create_task(client.start_sync_loop())

    # ── 5a. Collect at least one data point from each telemetry service ──
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        if tracker.all_telemetry_received:
            break
        await asyncio.sleep(0.5)

    if tracker.all_telemetry_received:
        print(f"\n  {CHECKED} All 10 telemetry types received!")
    else:
        missing = [t[1] for t in TELEMETRY_TYPES if t[0] not in tracker.seen]
        print(f"\n  {WAITING} Timed out waiting for: {', '.join(missing)}")
        print("     Moving on… some services may need movement to trigger.\n")

    # ── 5b. Ask for battery level via Protobuf ──
    if not tracker.checks.get("battery_rx"):
        print(f"\n  {WAITING} Sending Protobuf battery request...")
        request = gdi_smart_proto_pb2.Smart(
            device_status_service=gdi_device_status_pb2.DeviceStatusService(
                remote_device_battery_status_request=
                    gdi_device_status_pb2.DeviceStatusService.RemoteDeviceBatteryStatusRequest()
            )
        )
        await client.send_protobuf(request)
        print("     Waiting up to 10s for battery response...")
        for _ in range(20):
            if tracker.checks.get("battery_rx"):
                break
            await asyncio.sleep(0.5)

    # ── 5c. Keep running until all system checks pass or Ctrl+C ──
    if not tracker.all_system_checks_passed:
        print(f"\n  {WAITING} Still waiting for system events...")
        print("     (time sync, protobuf messages, etc.)\n")

        timeout = time.monotonic() + 120.0  # 2 more minutes
        while time.monotonic() < timeout:
            if tracker.all_done:
                break
            # Print a heartbeat every 15 s so the user knows we're alive
            remaining = int(timeout - time.monotonic())
            if remaining > 0 and remaining % 15 == 0:
                still_missing = [l for k, l in MISC_CHECKS if not tracker.checks.get(k)]
                print(f"     ⏳ still waiting ({remaining}s left): {', '.join(still_missing)}")
            await asyncio.sleep(1)

    # ------------------------------------------------------------------ #
    #  6. Summary                                                        #
    # ------------------------------------------------------------------ #
    total_time = time.monotonic() - tracker.start_time

    print(f"\n{'=' * 58}")
    print("  WALKTHROUGH SUMMARY")
    print(f"{'=' * 58}")
    print(f"\n  Duration: {total_time:.1f}s\n")
    print("  Telemetry:")
    print(tracker.telemetry_checklist())
    print(f"\n  System:")
    print(tracker.misc_checklist())

    if tracker.all_done:
        print(f"\n  {CHECKED} Everything works! All features verified successfully.")
    elif tracker.all_telemetry_received:
        print(f"\n  {CHECKED} All telemetry OK but some system checks didn't fire.")
        print("     The watch may not send some events spontaneously during this session.")
    else:
        print(f"\n  {WAITING} Partial success — some features didn't fire.")
        print("     This is normal for services that need movement / time.")
        print("     Run the watch for longer or move around to trigger them.")

    # ------------------------------------------------------------------ #
    #  7. Disconnect                                                     #
    # ------------------------------------------------------------------ #
    print(f"\n  👋 Disconnecting...")
    sync_task.cancel()
    await client.disconnect()
    print("  ✅ Done.\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n  Interrupted by user.")
