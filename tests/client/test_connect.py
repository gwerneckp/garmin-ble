"""Tests for the BLE connection flow (scanning, pairing, service discovery).

We mock BleakScanner and BleakClient so no real Bluetooth hardware is needed.
"""
import pytest
from unittest.mock import AsyncMock, PropertyMock
from garmin_ble.client import GarminClientBase, GarminClient


class TestConnect:
    """Verify GarminClient can find a watch, connect, and subscribe.

    Tests check: BleakScanner.discover returns a device, BleakClient.connect
    succeeds, the RX characteristic is found and subscribed to, and a
    CLOSE_ALL_REQ is sent to begin the handshake.
    """

    # ── GarminClient (scanning) tests ──────────────────────

    async def test_connect_success(self, mock_bleak_scanner, mock_bleak_client, mocker):
        """A normal connection via auto-discovery completes without errors."""
        mocker.patch("garmin_ble.client.base.BleakClient", return_value=mock_bleak_client)
        from tests.conftest import make_mock_services_with_rx_tx
        mock_bleak_client.services = make_mock_services_with_rx_tx()

        client = GarminClient()
        result = await client.connect()

        assert result is True
        assert client.client is not None
        mock_bleak_client.connect.assert_awaited_once()

    async def test_connect_no_garmin_device(self, mocker):
        """When no Garmin watch is found, connect() returns False."""
        mocker.patch(
            "garmin_ble.client.auto.BleakScanner.discover",
            new=AsyncMock(return_value={})
        )
        client = GarminClient()
        result = await client.connect()
        assert result is False

    async def test_connect_no_rx_char(self, mock_bleak_scanner, mock_bleak_client, mocker):
        """If the device has no Garmin service, connect() returns False."""
        mocker.patch("garmin_ble.client.base.BleakClient", return_value=mock_bleak_client)
        mock_bleak_client.services = []

        client = GarminClient()
        result = await client.connect()
        assert result is False

    # ── GarminClientBase (direct address) tests ─────────────────────────

    async def test_connect_direct_success(self, mock_bleak_client, mocker):
        """Connecting by direct address succeeds."""
        mocker.patch("garmin_ble.client.base.BleakClient", return_value=mock_bleak_client)
        from tests.conftest import make_mock_services_with_rx_tx
        mock_bleak_client.services = make_mock_services_with_rx_tx()

        client = GarminClientBase()
        result = await client.connect("AA:BB:CC:DD:EE:FF")

        assert result is True
        assert client.client is not None
        mock_bleak_client.connect.assert_awaited_once()

    async def test_connect_mtu_negotiation(self, mock_bleak_client, mocker):
        """MTU size is read and write limit is set correctly."""
        mocker.patch("garmin_ble.client.base.BleakClient", return_value=mock_bleak_client)
        from tests.conftest import make_mock_services_with_rx_tx
        mock_bleak_client.services = make_mock_services_with_rx_tx()
        type(mock_bleak_client).mtu_size = PropertyMock(return_value=100)

        client = GarminClientBase()
        await client.connect("AA:BB:CC:DD:EE:FF")
        assert client.max_write_size == 97

    async def test_connect_handshake_sent(self, mock_bleak_client, mocker):
        """connect() sends CLOSE_ALL_REQ as the first handshake."""
        mocker.patch("garmin_ble.client.base.BleakClient", return_value=mock_bleak_client)
        from tests.conftest import make_mock_services_with_rx_tx
        mock_bleak_client.services = make_mock_services_with_rx_tx()

        client = GarminClientBase()
        await client.connect("AA:BB:CC:DD:EE:FF")

        write_calls = mock_bleak_client.write_gatt_char.call_args_list
        assert len(write_calls) >= 1
        last_call = write_calls[-1]
        payload = last_call[0][1]
        assert payload[1] == 5  # CLOSE_ALL_REQ

    async def test_connect_subscribes_to_rx(self, mock_bleak_client, mocker):
        """start_notify is called on the RX characteristic."""
        mocker.patch("garmin_ble.client.base.BleakClient", return_value=mock_bleak_client)
        from tests.conftest import make_mock_services_with_rx_tx
        mock_bleak_client.services = make_mock_services_with_rx_tx()

        client = GarminClientBase()
        await client.connect("AA:BB:CC:DD:EE:FF")

        mock_bleak_client.start_notify.assert_awaited_once()
        rx_uuid = mock_bleak_client.start_notify.call_args[0][0]
        assert "2810" in rx_uuid

    async def test_connect_direct_timeout(self, mock_bleak_client, mocker):
        """Direct connection with explicit timeout forwards to BleakClient."""
        mocker.patch("garmin_ble.client.base.BleakClient", return_value=mock_bleak_client)
        from tests.conftest import make_mock_services_with_rx_tx
        mock_bleak_client.services = make_mock_services_with_rx_tx()

        client = GarminClientBase()
        result = await client.connect("AA:BB:CC:DD:EE:FF", timeout=10.0)

        assert result is True
        mock_bleak_client.connect.assert_awaited_once_with(timeout=10.0)

