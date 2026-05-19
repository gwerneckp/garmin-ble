"""Tests for the BLE handshake (MLR registration, control messages).

We are testing if: the MLR registration handshake completes correctly.

The handshake follows CLOSE_ALL → REGISTER_ML → dynamic handle assignment.
Control messages are sent on handle 0x00. GFDI messages on handle 0x01+.
"""
import asyncio
import struct
import pytest
from unittest.mock import AsyncMock
from garmin_ble.client import GarminClient
from garmin_ble.constants import GarminService


class TestHandshake:
    """We are testing if: the MLR registration handshake completes correctly.

    After CLOSE_ALL, the watch sends CLOSE_ALL_RESP (type 6), which triggers
    REGISTER_ML_REQ for each telemetry service.  The watch then replies with
    REGISTER_ML_RESP (type 1) assigning dynamic handles.
    """

    async def test_close_all_sent_on_connect(self, mock_bleak_scanner, mock_bleak_client, mocker):
        """We are testing if: CLOSE_ALL_REQ is written to the TX characteristic during connect.

        The first control message sent on connect is CLOSE_ALL_REQ (RequestType 5),
        written to the TX characteristic, which resets all prior MLR assignments
        on the watch.
        """
        mocker.patch("garmin_ble.client.base.BleakClient", return_value=mock_bleak_client)
        from tests.conftest import make_mock_services_with_rx_tx
        mock_bleak_client.services = make_mock_services_with_rx_tx()

        client = GarminClient()
        await client.connect("AA:BB:CC:DD:EE:FF")

        # The last write_gatt_char call is CLOSE_ALL_REQ
        write_calls = mock_bleak_client.write_gatt_char.call_args_list
        assert len(write_calls) >= 1
        payload = write_calls[-1][0][1]
        assert payload[1] == 5  # RequestType.CLOSE_ALL_REQ

    async def test_close_all_resp_triggers_gfdi_registration(self, mock_bleak_scanner, mock_bleak_client, mocker):
        """We are testing if: CLOSE_ALL_RESP triggers REGISTER_ML_REQ only for GFDI.

        After receiving CLOSE_ALL_RESP (type 6) on the control handle, the client
        must send REGISTER_ML_REQ only for GFDI (service code 1). All other
        services must be registered explicitly by the user.
        """
        mocker.patch("garmin_ble.client.base.BleakClient", return_value=mock_bleak_client)
        from tests.conftest import make_mock_services_with_rx_tx
        mock_bleak_client.services = make_mock_services_with_rx_tx()
        mock_bleak_client.write_gatt_char.reset_mock()

        client = GarminClient()
        await client.connect("AA:BB:CC:DD:EE:FF")

        # Simulate CLOSE_ALL_RESP from the watch
        close_all_resp = bytes([0x00, 0x06])
        await client._notify_handler(None, close_all_resp)
        await asyncio.sleep(0.01)  # let async tasks flush

        registration_codes = []
        for c in mock_bleak_client.write_gatt_char.call_args_list:
            payload = c[0][1]
            # REGISTER_ML_REQ has request_type=0 at byte 1 and is >= 13 bytes
            if len(payload) >= 13 and payload[1] == 0:
                code = struct.unpack('<h', payload[10:12])[0]
                registration_codes.append(code)

        # Only GFDI should be registered automatically
        assert len(registration_codes) == 1, \
            f"Expected 1 registration, got {len(registration_codes)}: {registration_codes}"
        assert GarminService.GFDI in registration_codes, f"GFDI not registered"
        assert GarminService.REALTIME_HR not in registration_codes
        assert GarminService.REALTIME_STEPS not in registration_codes

    async def test_register_ml_resp_stores_handle(self, mock_bleak_scanner, mock_bleak_client, mocker):
        """We are testing if: REGISTER_ML_RESP stores the handle-to-service mapping.

        When the watch replies with REGISTER_ML_RESP (type 1) with status=0 and
        assigned_handle=3 (for GFDI, service code=1), the client's service_handles
        dict must map handle 0x03 to GarminService.GFDI.
        """
        mocker.patch("garmin_ble.client.base.BleakClient", return_value=mock_bleak_client)
        from tests.conftest import make_mock_services_with_rx_tx
        mock_bleak_client.services = make_mock_services_with_rx_tx()

        client = GarminClient()
        await client.connect("AA:BB:CC:DD:EE:FF")

        # Simulate REGISTER_ML_RESP: service_code=0x0001 (GFDI), status=0, handle=0x03
        resp = bytes([
            0x00, 0x01,        # type = REGISTER_ML_RESP
            0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0x01, 0x00,        # service_code (little-endian) = 1
            0x00,              # status = 0 (success)
            0x03,              # assigned_handle = 3
        ])
        await client._notify_handler(None, resp)

        assert client.service_handles[0x03] == GarminService.GFDI

    async def test_register_ml_rejection(self, mock_bleak_scanner, mock_bleak_client, mocker):
        """We are testing if: a failed REGISTER_ML_RESP (non-zero status) is handled gracefully.

        If the watch replies with status != 0, the client must NOT store the handle
        mapping (no exception raised, no entry created).
        """
        mocker.patch("garmin_ble.client.base.BleakClient", return_value=mock_bleak_client)
        from tests.conftest import make_mock_services_with_rx_tx
        mock_bleak_client.services = make_mock_services_with_rx_tx()

        client = GarminClient()
        await client.connect("AA:BB:CC:DD:EE:FF")

        # REGISTER_ML_RESP with status=1 (failure)
        resp = bytes([
            0x00, 0x01,
            0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0x01, 0x00,
            0x01,              # status = 1 (failure)
            0x03,
        ])
        await client._notify_handler(None, resp)

        assert 0x03 not in client.service_handles
