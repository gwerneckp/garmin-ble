"""Tests for the BLE connection flow (scanning, pairing, service discovery).

We mock BleakScanner and BleakClient so no real Bluetooth hardware is needed.
"""
import pytest
from unittest.mock import AsyncMock, PropertyMock
from garmin_ble.client import GarminClient


class TestConnect:
    """We are testing if: the GarminClient can find a watch, connect, and subscribe.

    Technically: verify BleakScanner.discover returns a device, BleakClient.connect
    succeeds, the RX characteristic is found and subscribed to, and a CLOSE_ALL_REQ
    is sent to begin the handshake.
    """

    async def test_connect_success(self, mock_bleak_scanner, mock_bleak_client, mocker):
        """We are testing if: a normal connection completes without errors.

        We mock the scanner to return a "fenix 7", then check that connect()
        returns True, stores the client reference, and actually called
        BleakClient.connect.
        """
        mocker.patch("garmin_ble.client.BleakClient", return_value=mock_bleak_client)
        from tests.conftest import make_mock_services_with_rx_tx
        mock_bleak_client.services = make_mock_services_with_rx_tx()

        client = GarminClient()
        result = await client.connect()

        assert result is True
        assert client.client is not None
        mock_bleak_client.connect.assert_awaited_once()

    async def test_connect_no_garmin_device(self, mocker):
        """We are testing if: no Garmin watch is found during scanning.

        When BleakScanner.discover returns an empty dict, connect() should
        return False instead of crashing.
        """
        mocker.patch(
            "garmin_ble.client.BleakScanner.discover",
            new=AsyncMock(return_value={})
        )
        client = GarminClient()
        result = await client.connect()
        assert result is False

    async def test_connect_no_rx_char(self, mock_bleak_scanner, mock_bleak_client, mocker):
        """We are testing if: the watch is reachable but has no Garmin service.

        If the device has zero services, connect() should bail and return
        False because there is no RX characteristic to subscribe to.
        """
        mocker.patch("garmin_ble.client.BleakClient", return_value=mock_bleak_client)
        mock_bleak_client.services = []

        client = GarminClient()
        result = await client.connect()
        assert result is False

    async def test_connect_mtu_negotiation(self, mock_bleak_scanner, mock_bleak_client, mocker):
        """We are testing if: the MTU size is read and write limit is set correctly.

        GarminClient.max_write_size should be (mtu - 3) to leave room for
        the ATT header.  With mtu_size=100, expect max_write_size=97.
        """
        mocker.patch("garmin_ble.client.BleakClient", return_value=mock_bleak_client)
        from tests.conftest import make_mock_services_with_rx_tx
        mock_bleak_client.services = make_mock_services_with_rx_tx()
        type(mock_bleak_client).mtu_size = PropertyMock(return_value=100)

        client = GarminClient()
        await client.connect()
        assert client.max_write_size == 97

    async def test_connect_handshake_sent(self, mock_bleak_scanner, mock_bleak_client, mocker):
        """We are testing if: connect() sends CLOSE_ALL_REQ as the first handshake.

        The last GATT write during connect() should have RequestType.CLOSE_ALL_REQ
        as its second byte (the message type field).
        """
        mocker.patch("garmin_ble.client.BleakClient", return_value=mock_bleak_client)
        from tests.conftest import make_mock_services_with_rx_tx
        mock_bleak_client.services = make_mock_services_with_rx_tx()

        client = GarminClient()
        await client.connect()

        write_calls = mock_bleak_client.write_gatt_char.call_args_list
        assert len(write_calls) >= 1
        last_call = write_calls[-1]
        payload = last_call[0][1]
        assert payload[1] == 5  # CLOSE_ALL_REQ

    async def test_connect_subscribes_to_rx(self, mock_bleak_scanner, mock_bleak_client, mocker):
        """We are testing if: start_notify is called on the RX characteristic.

        After connecting, the client must subscribe to notifications on the
        Garmin RX UUID (6A4E2810-...) so it can receive data from the watch.
        """
        mocker.patch("garmin_ble.client.BleakClient", return_value=mock_bleak_client)
        from tests.conftest import make_mock_services_with_rx_tx
        mock_bleak_client.services = make_mock_services_with_rx_tx()

        client = GarminClient()
        await client.connect()

        mock_bleak_client.start_notify.assert_awaited_once()
        rx_uuid = mock_bleak_client.start_notify.call_args[0][0]
        assert "2810" in rx_uuid

