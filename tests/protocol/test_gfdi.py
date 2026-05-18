"""Tests for GFDI (Garmin File Delivery Interface) message building.

GFDI messages are the application-layer protocol wrapped in COBS frames.
"""
import struct
import pytest
from garmin_ble.gfdi import GfdiMessageBuilder


class TestProtobufAck:
    """build_protobuf_ack creates a Status Message (Type 5000) acknowledging
    a Protobuf Request (Type 5043)."""

    ACK_MIN_LEN = 2 + 2 + 2 + 1 + 2 + 4 + 1 + 1 + 2  # 17 bytes

    def test_message_structure(self):
        msg = GfdiMessageBuilder.build_protobuf_ack(request_id=5, data_offset=0)
        assert len(msg) == self.ACK_MIN_LEN

    def test_header_fields(self):
        """Verify header: length, msg_type (5000), ref_msg_type (5043)."""
        msg = GfdiMessageBuilder.build_protobuf_ack(request_id=5, data_offset=0)
        (packet_size,) = struct.unpack('<H', msg[0:2])
        (msg_type,) = struct.unpack('<H', msg[2:4])
        (ref_msg_type,) = struct.unpack('<H', msg[4:6])

        assert packet_size == len(msg) - 2  # total minus the 2-byte size field
        assert msg_type == 5000, "Must be Status Message type"
        assert ref_msg_type == 5043, "Must reference Protobuf Request type"

    def test_status_is_ack(self):
        msg = GfdiMessageBuilder.build_protobuf_ack(request_id=5, data_offset=0)
        status = msg[6]
        assert status == 0, "Status 0 = ACK"

    def test_request_id(self):
        msg = GfdiMessageBuilder.build_protobuf_ack(request_id=42, data_offset=0)
        (req_id,) = struct.unpack('<H', msg[7:9])
        assert req_id == 42

    def test_data_offset(self):
        msg = GfdiMessageBuilder.build_protobuf_ack(request_id=0, data_offset=999)
        (offset,) = struct.unpack('<I', msg[9:13])
        assert offset == 999

    def test_protobuf_chunk_status(self):
        msg = GfdiMessageBuilder.build_protobuf_ack(request_id=0, data_offset=0)
        chunk_status = msg[13]
        assert chunk_status == 0, "0 = KEPT"

    def test_protobuf_status_code(self):
        msg = GfdiMessageBuilder.build_protobuf_ack(request_id=0, data_offset=0)
        status_code = msg[14]
        assert status_code == 0, "0 = NO_ERROR"

    @pytest.mark.parametrize("request_id", [0, 1, 255, 65535])
    def test_various_request_ids(self, request_id):
        msg = GfdiMessageBuilder.build_protobuf_ack(request_id=request_id, data_offset=0)
        (req_id,) = struct.unpack('<H', msg[7:9])
        assert req_id == request_id

    @pytest.mark.parametrize("data_offset", [0, 1, 255, 65535, 1048576])
    def test_various_data_offsets(self, data_offset):
        msg = GfdiMessageBuilder.build_protobuf_ack(request_id=0, data_offset=data_offset)
        (offset,) = struct.unpack('<I', msg[9:13])
        assert offset == data_offset

    def test_crc_validates(self):
        """The embedded CRC must validate against the rest of the message."""
        from garmin_ble.crc import compute_crc
        msg = GfdiMessageBuilder.build_protobuf_ack(request_id=1, data_offset=100)
        crc_embedded = struct.unpack('<H', msg[-2:])[0]
        crc_computed = compute_crc(msg[:-2])
        assert crc_embedded == crc_computed, "Embedded CRC must match calculation"

    def test_multiple_acks_unique(self):
        """Different parameters must produce different messages."""
        msg1 = GfdiMessageBuilder.build_protobuf_ack(request_id=1, data_offset=0)
        msg2 = GfdiMessageBuilder.build_protobuf_ack(request_id=2, data_offset=0)
        assert msg1 != msg2, "Different request_ids must produce unique messages"
