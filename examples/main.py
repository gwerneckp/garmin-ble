import asyncio
import logging

from garmin_ble import GarminClient
from garmin_ble.logging import configure

def on_heart_rate(hr, resting_hr):
    print(f"❤️  HR: {hr} BPM (Resting: {resting_hr} BPM)")

def on_steps(steps, goal):
    print(f"👣 Steps: {steps} / {goal}")

def on_hrv(rr_interval):
    print(f"💓 HRV: {rr_interval} ms")

def on_protobuf(request_id, smart_message):
    print(f"\n📦 [GFDI] Request ID: {request_id}")
    print("-" * 40)
    print(smart_message)
    print("-" * 40)

async def main():
    # Set up library logging (stderr by default; INFO level)
    configure(level=logging.INFO)

    # 1. Initialize the client (auto-discovers the watch via BLE)
    client = GarminClient()

    # 2. Register our custom callback functions
    client.on("hr", on_heart_rate)
    client.on("steps", on_steps)
    client.on("hrv", on_hrv)
    client.on("protobuf", on_protobuf)

    # 3. Connect and run the sync loop
    success = await client.connect()
    if success:
        print("\nStarting live telemetry stream. Press Ctrl+C to exit.\n")
        await client.start_sync_loop()
    else:
        print("Failed to start sync.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting...")
