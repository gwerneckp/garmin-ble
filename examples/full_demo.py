import asyncio
import logging

from garmin_ble import GarminClient, GarminService
from garmin_ble.logging import configure

# 1. Telemetry Callbacks
def on_heart_rate(hr, resting_hr):
    print(f"❤️  HR: {hr} BPM (Resting: {resting_hr})")

def on_calories(total, active):
    print(f"🔥 Calories: {total} total ({active} active)")

def on_intensity(mod, vig):
    print(f"⚡ Intensity: {mod} mod, {vig} vig mins")

def on_stress(level):
    print(f"🧘 Stress: {level}/100")

def on_body_battery(level):
    print(f"🔋 Body Battery: {level}/100")

def on_accel(packet):
    ts = packet["timestamp_ms"]
    for i, (x, y, z) in enumerate(packet["samples"], 1):
        print(f"⌚ Accel sample {i} @ {ts}ms: X={x:5d}  Y={y:5d}  Z={z:5d}  "
              f"({x/1024:+.3f}g, {y/1024:+.3f}g, {z/1024:+.3f}g)")

# 2. Connection Lifecycle Callbacks
def on_disconnected():
    print("⚠️  BLE Link dropped! Automatic reconnection is active.")

async def main():
    configure(level=logging.DEBUG)

    client = GarminClient()

    # Register callbacks for services we care about
    client.on("hr", on_heart_rate)
    client.on("accel", on_accel)
    client.on("calories", on_calories)
    client.on("intensity", on_intensity)
    client.on("stress", on_stress)
    client.on("body_battery", on_body_battery)
    client.on("disconnected", on_disconnected)

    print("🚀 Garmin Advanced Feature Showcase")
    print("   - Automatic Reconnection")
    print("   - Keep-alive Heartbeat")
    print("   - Time Synchronization")
    print("   - Manual service registration for Accel / Calories / Intensity")
    print("-" * 50)

    if await client.connect():
        print("\n✅ Connected! Starting sync loop.\n")

        # --- Manually start high-volume / user-choice services ---
        # These are NOT registered automatically to avoid unwanted
        # data streaming. Call register_and_start_service() for each
        # one you want:
        print("  → Registering & starting accelerometer manually ...")
        await client.register_and_start_service(GarminService.REALTIME_ACCELEROMETER)

        print("  → Registering & starting calories manually ...")
        await client.register_and_start_service(GarminService.REALTIME_CALORIES)

        print("  → Registering & starting intensity manually ...")
        await client.register_and_start_service(GarminService.REALTIME_INTENSITY)

        print("\n  ℹ️  All requested services are now live. Press Ctrl+C to stop.\n")

        try:
            await client.start_sync_loop()
        except asyncio.CancelledError:
            pass
    else:
        print("❌ Could not find or connect to a Garmin watch.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopping showcase...")
