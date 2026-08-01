"""Shared fixtures.

The simulated transport speaks the real protocol, so tests drive a `Watch`
end-to-end against real framing and routing rather than against a mock that
merely records calls.

`mock_ble` and `mock_scan` are for the cases that genuinely need the
bleak-specific transport: characteristic discovery, MTU, and scan filtering.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, PropertyMock

from garmin_ble import Watch
from garmin_ble.constants import GARMIN_BASE_UUID


# ── simulated watch ──────────────────────────────────────────────────────────


@pytest.fixture
async def watch():
    """A connected `Watch` backed by the full-featured simulator."""
    session = Watch.simulated(profile="fenix7", seed=1234)
    connected = await session.open()
    try:
        yield connected
    finally:
        await session.aclose()


@pytest.fixture
async def limited_watch():
    """A connected `Watch` whose model lacks SpO2 and the accelerometer."""
    session = Watch.simulated(profile="venu3", seed=1234)
    connected = await session.open()
    try:
        yield connected
    finally:
        await session.aclose()


# ── bleak doubles, for the BLE transport only ────────────────────────────────


def make_gatt_service(start: int = 0x2810, count: int = 5) -> MagicMock:
    """A mock GATT service exposing sequential Garmin-style characteristics."""
    service = MagicMock()
    service.characteristics = [
        MagicMock(uuid=GARMIN_BASE_UUID.format(i).lower())
        for i in range(start, start + count)
    ]
    return service


def make_garmin_services():
    """The RX (0x281x) and TX (0x282x) characteristic groups a watch exposes."""
    return [make_gatt_service(0x2810, 5), make_gatt_service(0x2820, 5)]


@pytest.fixture
def mock_ble(mocker):
    """Patch `BleakClient` and return the instance the transport will receive."""
    client = MagicMock()
    client.address = "AA:BB:CC:DD:EE:FF"
    client.is_connected = True
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.start_notify = AsyncMock()
    client.write_gatt_char = AsyncMock()
    client.services = make_garmin_services()
    type(client).mtu_size = PropertyMock(return_value=0)
    mocker.patch("garmin_ble.transport.ble.BleakClient", return_value=client)
    return client


@pytest.fixture
def mock_scan(mocker):
    """Patch `BleakScanner.discover`. Call the returned factory with devices."""

    def install(*devices):
        """devices: (name, address) pairs; pass nothing for an empty scan."""
        found = {}
        for name, address in devices:
            device = MagicMock()
            device.name = name
            device.address = address
            adv = MagicMock()
            adv.local_name = name
            adv.rssi = -50
            found[address] = (device, adv)
        mocker.patch(
            "garmin_ble.transport.ble.BleakScanner.discover",
            new=AsyncMock(return_value=found),
        )
        return found

    return install
