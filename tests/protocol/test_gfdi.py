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
        msg = GfdiMessageBuilder.build_protobuf_ack(ref_msg_type=5043, request_id=5, data_offset=0)
        assert len(msg) == self.ACK_MIN_LEN

    def test_header_fields(self):
        """Verify header: length, msg_type (5000), ref_msg_type (5043)."""
        msg = GfdiMessageBuilder.build_protobuf_ack(ref_msg_type=5043, request_id=5, data_offset=0)
        (packet_size,) = struct.unpack('<H', msg[0:2])
        (msg_type,) = struct.unpack('<H', msg[2:4])
        (ref_msg_type,) = struct.unpack('<H', msg[4:6])

        assert packet_size == len(msg)  # total size including the 2-byte size field
        assert msg_type == 5000, "Must be Status Message type"
        assert ref_msg_type == 5043, "Must reference Protobuf Request type"

    def test_status_is_ack(self):
        msg = GfdiMessageBuilder.build_protobuf_ack(ref_msg_type=5043, request_id=5, data_offset=0)
        status = msg[6]
        assert status == 0, "Status 0 = ACK"

    def test_request_id(self):
        msg = GfdiMessageBuilder.build_protobuf_ack(ref_msg_type=5043, request_id=42, data_offset=0)
        (req_id,) = struct.unpack('<H', msg[7:9])
        assert req_id == 42

    def test_data_offset(self):
        msg = GfdiMessageBuilder.build_protobuf_ack(ref_msg_type=5043, request_id=0, data_offset=999)
        (offset,) = struct.unpack('<I', msg[9:13])
        assert offset == 999

    def test_protobuf_chunk_status(self):
        msg = GfdiMessageBuilder.build_protobuf_ack(ref_msg_type=5043, request_id=0, data_offset=0)
        chunk_status = msg[13]
        assert chunk_status == 0, "0 = KEPT"

    def test_protobuf_status_code(self):
        msg = GfdiMessageBuilder.build_protobuf_ack(ref_msg_type=5043, request_id=0, data_offset=0)
        status_code = msg[14]
        assert status_code == 0, "0 = NO_ERROR"

    @pytest.mark.parametrize("request_id", [0, 1, 255, 65535])
    def test_various_request_ids(self, request_id):
        msg = GfdiMessageBuilder.build_protobuf_ack(ref_msg_type=5043, request_id=request_id, data_offset=0)
        (req_id,) = struct.unpack('<H', msg[7:9])
        assert req_id == request_id

    @pytest.mark.parametrize("data_offset", [0, 1, 255, 65535, 1048576])
    def test_various_data_offsets(self, data_offset):
        msg = GfdiMessageBuilder.build_protobuf_ack(ref_msg_type=5043, request_id=0, data_offset=data_offset)
        (offset,) = struct.unpack('<I', msg[9:13])
        assert offset == data_offset

    def test_crc_validates(self):
        """The embedded CRC must validate against the rest of the message."""
        from garmin_ble.crc import compute_crc
        msg = GfdiMessageBuilder.build_protobuf_ack(ref_msg_type=5043, request_id=1, data_offset=100)
        crc_embedded = struct.unpack('<H', msg[-2:])[0]
        crc_computed = compute_crc(msg[:-2])
        assert crc_embedded == crc_computed, "Embedded CRC must match calculation"

    def test_multiple_acks_unique(self):
        """Different parameters must produce different messages."""
        msg1 = GfdiMessageBuilder.build_protobuf_ack(ref_msg_type=5043, request_id=1, data_offset=0)
        msg2 = GfdiMessageBuilder.build_protobuf_ack(ref_msg_type=5043, request_id=2, data_offset=0)
        msg3 = GfdiMessageBuilder.build_protobuf_ack(ref_msg_type=5043, request_id=1, data_offset=1)
        assert msg1 != msg2, "Different request_ids must produce unique messages"
        assert msg1 != msg3, "Different offsets must produce unique messages"


class TestStatusAck:
    """build_status_ack creates a simple ACK (Type 5000) for any GFDI message type."""

    STATUS_ACK_MIN_LEN = 2 + 2 + 2 + 1 + 2  # 9 bytes

    def test_header_fields(self):
        msg = GfdiMessageBuilder.build_status_ack(ref_message_type=5052)
        assert len(msg) == self.STATUS_ACK_MIN_LEN
        (packet_size,) = struct.unpack('<H', msg[0:2])
        (msg_type,) = struct.unpack('<H', msg[2:4])
        (ref_type,) = struct.unpack('<H', msg[4:6])
        assert msg_type == 5000
        assert ref_type == 5052

    def test_status_is_ack(self):
        msg = GfdiMessageBuilder.build_status_ack(ref_message_type=5030)
        status = msg[6]
        assert status == 0, "Status 0 = ACK"

    @pytest.mark.parametrize("ref_type", [5000, 5030, 5043, 5052, 6000])
    def test_various_ref_types(self, ref_type):
        msg = GfdiMessageBuilder.build_status_ack(ref_message_type=ref_type)
        (ref_read,) = struct.unpack('<H', msg[4:6])
        assert ref_read == ref_type

    def test_crc(self):
        from garmin_ble.crc import compute_crc
        msg = GfdiMessageBuilder.build_status_ack(ref_message_type=5043)
        crc_embedded = struct.unpack('<H', msg[-2:])[0]
        crc_computed = compute_crc(msg[:-2])
        assert crc_embedded == crc_computed


class TestSystemEvent:
    """build_system_event creates a SYSTEM_EVENT message (Type 5030)."""

    SYS_EVENT_MIN_LEN = 2 + 2 + 1 + 1 + 2  # 8 bytes

    def test_message_structure(self):
        msg = GfdiMessageBuilder.build_system_event(event_type=16, value=0)
        assert len(msg) == self.SYS_EVENT_MIN_LEN
        (packet_size,) = struct.unpack('<H', msg[0:2])
        (msg_type,) = struct.unpack('<H', msg[2:4])
        assert msg_type == 5030, "Must be SYSTEM_EVENT type"

    def test_event_type_and_value(self):
        msg = GfdiMessageBuilder.build_system_event(event_type=16, value=0)
        assert msg[4] == 16
        assert msg[5] == 0

    @pytest.mark.parametrize("event_type,value", [
        (0, 0),   # SYNC_COMPLETE
        (6, 0),   # HOST_DID_ENTER_FOREGROUND
        (7, 0),   # HOST_DID_ENTER_BACKGROUND
        (8, 0),   # SYNC_READY
        (16, 0),  # TIME_UPDATED
    ])
    def test_various_event_types(self, event_type, value):
        msg = GfdiMessageBuilder.build_system_event(event_type=event_type, value=value)
        assert msg[4] == event_type
        assert msg[5] == value

    def test_crc(self):
        from garmin_ble.crc import compute_crc
        msg = GfdiMessageBuilder.build_system_event(event_type=16, value=0)
        crc_embedded = struct.unpack('<H', msg[-2:])[0]
        crc_computed = compute_crc(msg[:-2])
        assert crc_embedded == crc_computed


class TestTimeResponse:
    """build_time_response creates a time-sync ACK (Type 5000, ref 5052)."""

    TIME_RESP_MIN_LEN = 2 + 2 + 2 + 1 + 4 + 4 + 4 + 4 + 4 + 2  # 29 bytes

    def test_message_structure(self):
        msg = GfdiMessageBuilder.build_time_response()
        assert len(msg) == self.TIME_RESP_MIN_LEN
        (packet_size,) = struct.unpack('<H', msg[0:2])
        (msg_type,) = struct.unpack('<H', msg[2:4])
        assert msg_type == 5000, "Must be Status Message type"

    def test_header_fields(self):
        msg = GfdiMessageBuilder.build_time_response()
        (msg_type,) = struct.unpack('<H', msg[2:4])
        (ref_type,) = struct.unpack('<H', msg[4:6])
        status = msg[6]
        assert msg_type == 5000
        assert ref_type == 5052, "Must reference CURRENT_TIME_REQUEST"
        assert status == 0, "Status 0 = ACK"

    def test_timestamp_is_reasonable(self):
        """Garmin epoch offset is 631065600 (1990-01-01).
        Current timestamp should be > 0 and < 2G (overflow-safe)."""
        msg = GfdiMessageBuilder.build_time_response()
        (garmin_ts,) = struct.unpack('<I', msg[11:15])
        assert 100_000_000 < garmin_ts < 2_000_000_000

    def test_timezone_offset(self):
        msg = GfdiMessageBuilder.build_time_response()
        (offset,) = struct.unpack('<i', msg[15:19])
        # Offset should be reasonable: -43200 to 50400 seconds
        assert -43200 <= offset <= 50400

    def test_crc(self):
        from garmin_ble.crc import compute_crc
        msg = GfdiMessageBuilder.build_time_response()
        crc_embedded = struct.unpack('<H', msg[-2:])[0]
        crc_computed = compute_crc(msg[:-2])
        assert crc_embedded == crc_computed
