# Garmin BLE Sync (Python)

A Python library reverse-engineered from Gadgetbridge, allowing direct Bluetooth Low Energy (BLE) communication with modern Garmin watches and heart rate monitors using their proprietary protocol (GFDI V2).

## Features

- **Live Telemetry:** Connects to the watch to stream real-time sensor data over BLE without requiring the Garmin Connect app.
  - Heart Rate & Resting Heart Rate
  - Daily Steps & Goal
  - Heart Rate Variability (HRV)
  - Blood Oxygen (SpO2)
  - Respiration Rate
- **Protocol Decoding:**
  - Automated Handshake (`CLOSE_ALL`, `REGISTER_ML`)
  - MLR (Multi-Link Routing) packet multiplexing.
  - COBS (Consistent Overhead Byte Stuffing) Decoder implementation.
  - Compiled Python Protobufs to natively read Garmin's `gdi_smart_proto`.

## Installation

```bash
# Clone the repository
git clone <repo_url>
cd garmin_ble

# Install dependencies (requires bleak and protobuf)
pip install .
```

## Quick Start

See the `examples/` directory for full usage.

```python
import asyncio
from garmin_ble import GarminClient

def on_heart_rate(hr, resting_hr):
    print(f"HR: {hr} BPM (Resting: {resting_hr} BPM)")

async def main():
    client = GarminClient()
    client.on("hr", on_heart_rate)

    if await client.connect():
        await client.start_sync_loop()

asyncio.run(main())
```

## Status & Roadmap

See `ROADMAP.md` for the current feature parity status with Gadgetbridge. We are currently moving through Phase 3: Protobuf Settings & Device State communication.

## Acknowledgements & License

This project would not be possible without the incredible reverse-engineering efforts of the [Gadgetbridge](https://codeberg.org/Freeyourgadget/Gadgetbridge) team. The Python implementation of the Garmin BLE protocol, COBS decoding, and the `.proto` schemas are heavily based on their original Java source code. 

Because this project is a derivative work of Gadgetbridge, it inherits their open-source licensing. This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**. See the `LICENSE` file for details.
