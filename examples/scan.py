#!/usr/bin/env python3
"""Scan for BLE devices and show which ones garmin-ble would connect to.

Usage:
    python examples/scan.py
    python examples/scan.py --timeout 20

Run this first when discovery fails. It prints every device in range, marks the
ones whose advert name looks like a Garmin wearable, and says explicitly if none
matched — which is almost always one of:

  * the watch is connected to your phone (Garmin allows one BLE client at a time)
  * the watch is still connected to *this* machine from an earlier run
  * Bluetooth permissions are not granted to your terminal
"""

import argparse
import asyncio

from bleak import BleakScanner

from garmin_ble.transport.ble import GARMIN_NAME_HINTS, looks_like_garmin


async def main(timeout: float) -> None:
    print(f"Scanning for {timeout:.0f}s...\n")
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)

    if not found:
        print("No BLE devices at all.")
        print("That usually means Bluetooth is off, or your terminal lacks the")
        print("Bluetooth permission (macOS: System Settings → Privacy & Security).")
        return

    matches = []
    rows = []
    for address, (device, adv) in sorted(found.items()):
        name = device.name or getattr(adv, "local_name", None)
        rssi = getattr(adv, "rssi", None)
        hit = looks_like_garmin(name)
        if hit:
            matches.append((name, address))
        rows.append((hit, name or "<unnamed>", address, rssi))

    rows.sort(key=lambda r: (not r[0], r[1].lower()))
    for hit, name, address, rssi in rows:
        mark = "✅" if hit else "  "
        signal = f"{rssi:4d} dBm" if isinstance(rssi, int) else "        "
        print(f"{mark} {name:<32s} {address}  {signal}")

    print(f"\n{len(found)} device(s) seen, {len(matches)} matching a Garmin name.")

    if not matches:
        print("\nNo Garmin-looking device found. Names are matched against:")
        print(f"  {', '.join(GARMIN_NAME_HINTS)}")
        print("\nIf your watch is in the list above under a different name, connect")
        print("to it directly and skip the name filter:")
        print("  Watch.at('<address>')      # by BLE address")
        print("  Watch.named('<pattern>')   # by glob on the advert name")
        print("\nIf it is not in the list at all, it is not advertising — it is")
        print("probably still connected to your phone, or to this machine from an")
        print("earlier run. Disconnect it there and scan again.")
    else:
        name, address = matches[0]
        print(f"\nWatch.discover() would connect to: {name} [{address}]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scan for Garmin watches over BLE.")
    parser.add_argument("--timeout", type=float, default=15.0, help="scan duration in seconds")
    args = parser.parse_args()
    asyncio.run(main(args.timeout))
