# <picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/logo.png">
  <source media="(prefers-color-scheme: light)" srcset="assets/logo.png">
  <img alt="garmin-ble logo" src="assets/logo.png" width="100" height="100" align="left" style="padding-right: 8px;">
</picture> garmin-ble

[![PyPI](https://img.shields.io/pypi/v/garmin-ble)](https://pypi.org/project/garmin-ble/)
[![Python Version](https://img.shields.io/pypi/pyversions/garmin-ble)](https://pypi.org/project/garmin-ble/)
[![License](https://img.shields.io/pypi/l/garmin-ble)](https://www.gnu.org/licenses/agpl-3.0)

A clean-room Python implementation of Garmin's proprietary BLE protocol (GFDI V2). Stream live telemetry from your Garmin watch directly to your computer: no cloud, no phone, no Garmin Connect required.

---

## Features

- **Live Telemetry** — stream real-time sensor data over BLE without Garmin Connect:
  - ❤️ Heart Rate & Resting Heart Rate
  - 🚶 Daily Steps & Goal
  - 📊 Heart Rate Variability (HRV)
  - 🫁 Blood Oxygen (SpO2)
  - 🌬️ Respiration Rate
  - 🔥 Calories (total & active)
  - ⚡ Intensity Minutes
  - 🧘 Stress Level
  - 🔋 Body Battery
  - ⌚ Accelerometer
- **On-Demand Service Registration** — telemetry services start only when you ask. No unwanted data streaming.
- **Protocol Decoding** — full implementation of the Garmin GFDI V2 stack:
  - Automated handshake (`CLOSE_ALL`, `REGISTER_ML`)
  - MLR (Multi-Link Routing) packet multiplexing
  - COBS (Consistent Overhead Byte Stuffing) encoding/decoding
  - Compiled Protobufs for `gdi_smart_proto`
  - CRC16 integrity checking
- **Automatic Reconnection** — survives BLE drops with exponential backoff
- **Keep-Alive Heartbeat** — periodic time-sync to maintain the link
- **Hackable** — pure Python, no binary blobs, no proprietary SDKs

---

## Installation

```bash
pip install garmin-ble
```

Or install from source with dev dependencies:

```bash
git clone https://github.com/gwerneckp/garmin-ble.git
cd garmin-ble
pip install -e ".[dev]"
```

---

## Quick Start

```python
import asyncio
from garmin_ble import GarminClient, GarminService

def on_heart_rate(hr, resting_hr):
    print(f"❤️  {hr} BPM (Resting: {resting_hr} BPM)")

async def main():
    client = GarminClient()
    client.on("hr", on_heart_rate)

    if await client.connect():
        # Register and start only the services you want
        await client.register_and_start_service(GarminService.REALTIME_HR)
        print("Connected! Streaming data...")
        await client.start_sync_loop()

asyncio.run(main())
```

> [!TIP]
> Make sure your watch is **not** connected to your phone via Bluetooth — Garmin watches only allow one BLE connection at a time.

> [!IMPORTANT]
> Telemetry services are **not** auto-started. Use `register_and_start_service()` for each service you need (e.g., `GarminService.REALTIME_STEPS`, `GarminService.REALTIME_ACCELEROMETER`). Only the GFDI control channel is registered automatically.

See the [`examples/`](./examples/) directory for more complete usage patterns — including `basic_telemetry.py` (simple start), `full_demo.py` (advanced features), and `tilt_volume.py` (accelerometer-driven Mac volume control).

---

## Status & Roadmap

See the [GitHub Issues](https://github.com/gwerneckp/garmin-ble/issues) for the
full breakdown of planned features, known gaps, and in-progress work. Milestones
map to release versions:

| Phase | Goal | Status |
|-------|------|--------|
| 1 | 🏗️ BLE transport & handshake | ✅ Done |
| 2 | 📡 Live telemetry streaming | ✅ Done |
| 3 | 🧠 Protobuf settings & device state | 🔄 In progress |
| 4 | 🔔 Notifications & media control | ⏳ Planned |
| 5 | 📁 File transfers (FIT / GPX downloads) | ⏳ Planned |
| 6 | 🗄️ Persistence & dashboard | ⏳ Planned |

---

## Design Philosophy

`garmin-ble` is a **wire-protocol library**, not a feature-complete application. It knows how to encode, send, decode, and respond to Garmin's BLE protobufs — and nothing more.

This means:
- The library does **not** fetch weather from OpenWeatherMap, sync calendars via CalDAV, or play sounds when the watch says "find my phone".
- Instead it provides the building blocks — protobuf encode/decode, callbacks for incoming messages, and transport helpers.
- **Callers** wire up the OS integration, external APIs, and user-facing features.

This keeps the library focused, testable, and free of the endless feature creep that plagues integration-heavy projects.

**Consider using Gadgetbridge instead if:** you want a full-featured open-source replacement for the Garmin Connect app on your Android phone.

## Project Mission

**Own your data.** Garmin devices capture a wealth of physiological data, but Garmin Connect locks it behind a cloud service. This library gives you direct, programmatic access to your watch over BLE — no internet required.

---

## Acknowledgements & License

This project builds on the extraordinary reverse-engineering work of the [Gadgetbridge](https://codeberg.org/Freeyourgadget/Gadgetbridge) team. The protocol logic, COBS decoding, and `.proto` schemas are derived from their open-source Java implementation.

Licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)** — see [`LICENSE`](./LICENSE) for details.
