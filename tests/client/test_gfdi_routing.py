"""Tests for GFDI (Garmin File Delivery Interface) protobuf routing.

GFDI messages arrive on the GFDI handle (0x01), wrapped in COBS framing.
The client must decode the COBS frame, parse the GFDI message header,
detect a Protobuf Request (type 5043), build and send an ACK (type 5000),
and fire the 'protobuf' event with the deserialised message.
"""
import asyncio
import struct
import pytest
from unittest.mock import AsyncMock, MagicMock

from garmin_ble.client import GarminClient
from garmin_ble.constants import GarminService


@pytest.fixture
async def connected_client(mock_bleak_scanner, mock_bleak_client, mocker):
    """Return a GarminClient that has completed the connect() handshake."""
    mocker.patch("garmin_ble.client.base.BleakClient", return_value=mock_bleak_client)
    from tests.conftest import make_mock_services_with_rx_tx
    mock_bleak_client.services = make_mock_services_with_rx_tx()
    mock_bleak_client.write_gatt_char = AsyncMock()

    client = GarminClient()
    await client.connect("AA:BB:CC:DD:EE:FF")
    notify_handler = mock_bleak_client.start_notify.call_args[0][1]
    return client, notify_handler


class TestGfdiProtobuf:
    """We are testing if: GFDI protobuf requests are ACKed and routed to callbacks.

    A GFDI protobuf request arrives as:
      COBS( GFDI_header( msg_type=5043, request_id, offsets…) + proto_body )
    The client must reply with a Status Message (type 5000) ACK and fire 'protobuf'.
    """

    @pytest.fixture
    async def gfdi_client(self, connected_client):
        """Connected client with GFDI handle (0x01) mapped."""
        client, notify_handler = connected_client
        client.service_handles = {0x01: GarminService.GFDI}
        return client, notify_handler

    async def test_gfdi_protobuf_request_triggers_ack(self, gfdi_client, mock_bleak_client, mocker):
        """We are testing if: receiving a protobuf request causes an ACK write.

        When a GFDI protobuf Request (type 5043) arrives on the GFDI handle,
        the client must write a Status Message (type 5000) ACK back to the watch.
        """
        from garmin_ble.cobs import CobsCoDec

        client, notify = gfdi_client
        smart_msg = mocker.MagicMock()
        mocker.patch(
            "garmin_ble.client.base.gdi_smart_proto_pb2.Smart",
            return_value=smart_msg,
        )
        mock_bleak_client.write_gatt_char.reset_mock()

        proto_body = b"\x08\x00"
        gfdi_payload = struct.pack('<HH', 0, 5043)
        gfdi_payload += struct.pack('<H', 42)
        gfdi_payload += struct.pack('<IIII', 0, len(proto_body), len(proto_body), 0)
        gfdi_payload += proto_body

        pkt = bytes([0x80 | (0x01 << 4)]) + CobsCoDec.encode(gfdi_payload)
        await notify(None, pkt)

        # Allow async tasks to flush
        await asyncio.sleep(0.01)

        assert mock_bleak_client.write_gatt_char.awaited
        smart_msg.ParseFromString.assert_called_once()

    async def test_protobuf_callback_fires(self, gfdi_client, mocker):
        """We are testing if: the 'protobuf' event fires with (request_id, parsed_msg).

        After decoding the GFDI frame and parsing the protobuf, the client must
        invoke registered 'protobuf' callbacks with the request_id and Smart message.
        """
        from garmin_ble.cobs import CobsCoDec

        client, notify = gfdi_client
        results = []
        client.on("protobuf", lambda rid, msg: results.append((rid, msg)))

        smart_msg = mocker.MagicMock()
        mocker.patch(
            "garmin_ble.client.base.gdi_smart_proto_pb2.Smart",
            return_value=smart_msg,
        )

        proto_body = b"\x08\x00"
        gfdi_payload = struct.pack('<HH', 0, 5043)
        gfdi_payload += struct.pack('<H', 7)
        gfdi_payload += struct.pack('<IIII', 0, len(proto_body), len(proto_body), 0)
        gfdi_payload += proto_body

        pkt = bytes([0x80 | (0x01 << 4)]) + CobsCoDec.encode(gfdi_payload)
        await notify(None, pkt)

        await asyncio.sleep(0.01)
        assert len(results) == 1
        assert results[0][0] == 7
        assert results[0][1] is smart_msg
