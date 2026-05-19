import struct
import time
from datetime import datetime
from .crc import compute_crc

GARMIN_EPOCH_OFFSET = 631065600  # 1990-01-01 00:00:00 UTC


def _garmin_timestamp() -> int:
    """Return current UTC time as a Garmin epoch timestamp (seconds since 1990-01-01)."""
    return int(time.time()) - GARMIN_EPOCH_OFFSET


def _build_frame(message_type: int, payload: bytes) -> bytes:
    """Wrap a payload in the GFDI frame: packet_size (H) + type (H) + payload + CRC16 (H).

    packet_size includes the type (2) + payload + CRC (2), but NOT itself.
    """
    packet_size = 2 + len(payload) + 2
    buffer = struct.pack('<HH', packet_size, message_type) + payload
    crc = compute_crc(buffer)
    return buffer + struct.pack('<H', crc)


class GfdiMessageBuilder:
    """Factory for constructing GFDI-protocol wire messages."""

    @staticmethod
    def build_message(message_type: int, payload: bytes) -> bytes:
        """Wraps a payload in the GFDI packet structure (size + type + payload + crc)."""
        return _build_frame(message_type, payload)

    @staticmethod
    def build_status_ack(ref_message_type: int) -> bytes:
        """Build a simple ACK (type 5000, status=0) for a referenced GFDI message type."""
        # Format: ref_msg_type (H), status (b)
        payload = struct.pack('<Hb', ref_message_type, 0)
        return _build_frame(5000, payload)

    @staticmethod
    def build_protobuf_ack(request_id: int, data_offset: int) -> bytes:
        """
        Builds a GFDI Status Message (Type 5000) specifically as an ACK for
        a Protobuf Request (Type 5043).
        """
        # Format: ref_msg_type (H), status (b),
        #         request_id (H), data_offset (I), chunk_status (b), status_code (b)
        payload = struct.pack('<HbHIbb',
            5043,       # ref_msg_type (PROTOBUF_REQUEST)
            0,          # status = ACK
            request_id,
            data_offset,
            0,          # protobuf_chunk_status = KEPT
            0,          # protobuf_status_code = NO_ERROR
        )
        return _build_frame(5000, payload)

    @staticmethod
    def build_time_response() -> bytes:
        """Build a time-sync response to the watch's CURRENT_TIME_REQUEST (5052).

        Ported from ``CurrentTimeRequestMessage.generateOutgoing()`` in the
        legacy Gadgetbridge codebase.

        Wire format (Status message type 5000):
            ref_msg_type (H) = 5052
            status (b) = 0 (ACK)
            referenceID (I) = 0
            garminTimestamp (I) = seconds since 1990-01-01 UTC
            timeZoneOffset (i) = total UTC offset in seconds
            dstEnd (i) = next DST transition end (Garmin epoch, 0 if none)
            dstStart (i) = next DST transition start (Garmin epoch, 0 if none)
        """
        garmin_ts = _garmin_timestamp()

        # Local UTC offset (positive east of UTC)
        local_now = datetime.now()
        utc_offset = int(local_now.astimezone().utcoffset().total_seconds())

        payload = struct.pack('<HbIiiii',
            5052,       # ref_msg_type = CURRENT_TIME_REQUEST
            0,          # status = ACK
            0,          # referenceID
            garmin_ts,
            utc_offset,
            0,          # dstEnd (simplified for now)
            0,          # dstStart (simplified for now)
        )
        return _build_frame(5000, payload)

    @staticmethod
    def build_system_event(event_type: int, value: int = 0) -> bytes:
        """Build a SYSTEM_EVENT message (type 5030)."""
        payload = struct.pack('<BB', event_type, value)
        return _build_frame(5030, payload)
