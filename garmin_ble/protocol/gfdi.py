"""GFDI message framing: builders for outgoing frames, parsers for incoming.

A GFDI frame is ``size (H) | type (H) | payload | crc16 (H)``, carried inside
COBS framing on the GFDI MLR handle.

Both directions live here so one file describes the whole message format, and
so decoding can be tested without a BLE stack.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..constants import GarminMessage
from .crc import compute_crc

GARMIN_EPOCH_OFFSET = 631065600  # 1990-01-01 00:00:00 UTC


def _garmin_timestamp() -> int:
    """Return current UTC time as a Garmin epoch timestamp (seconds since 1990-01-01)."""
    return int(time.time()) - GARMIN_EPOCH_OFFSET


def _build_frame(message_type: int, payload: bytes) -> bytes:
    """Wrap a payload in the GFDI frame: packet_size (H) + type (H) + payload + CRC16 (H).

    packet_size includes the size field (2) + type (2) + payload + CRC (2).
    """
    packet_size = 2 + 2 + len(payload) + 2
    frame_body = struct.pack('<HH', packet_size, message_type) + payload
    crc = compute_crc(frame_body)
    return frame_body + struct.pack('<H', crc)


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
    def build_protobuf_ack(ref_msg_type: int, request_id: int, data_offset: int) -> bytes:
        """
        Builds a GFDI Status Message (Type 5000) specifically as an ACK for
        a Protobuf Request or Response.
        """
        # Format: ref_msg_type (H), status (b),
        #         request_id (H), data_offset (I), chunk_status (b), status_code (b)
        payload = struct.pack('<HbHIbb',
            ref_msg_type,
            0,          # status = ACK
            request_id,
            data_offset,
            0,          # protobuf_chunk_status = KEPT
            0,          # protobuf_status_code = NO_ERROR
        )
        return _build_frame(5000, payload)

    @staticmethod
    def build_protobuf_response(request_id: int, data_offset: int, total_length: int, proto_bytes: bytes) -> bytes:
        """Build a PROTOBUF_RESPONSE (5044) GFDI frame."""
        payload = struct.pack('<HIIi', request_id, data_offset, total_length, len(proto_bytes)) + proto_bytes
        return _build_frame(5044, payload)

    @staticmethod
    def build_protobuf_request(request_id: int, data_offset: int, total_length: int, proto_bytes: bytes) -> bytes:
        """Build a PROTOBUF_REQUEST (5043) GFDI frame."""
        payload = struct.pack('<HIIi', request_id, data_offset, total_length, len(proto_bytes)) + proto_bytes
        return _build_frame(5043, payload)

    @staticmethod
    def build_time_response() -> bytes:
        """Build a time-sync response to the watch's CURRENT_TIME_REQUEST (5052).

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


# ── decoding ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GfdiMessage:
    """A decoded GFDI frame: its type and the payload after the header."""

    type: int
    payload: bytes
    raw: bytes

    @property
    def name(self) -> str:
        """The ``GarminMessage`` name for this type, or ``type=NNNN``."""
        try:
            return GarminMessage(self.type).name
        except ValueError:
            return f"type={self.type}"


@dataclass(frozen=True)
class ProtobufFrame:
    """The header of a PROTOBUF_REQUEST/RESPONSE, plus its protobuf bytes.

    Garmin chunks large protobufs across several frames; ``is_complete`` is true
    only for a single-frame message, which is the case this library handles.
    """

    message_type: int
    request_id: int
    data_offset: int
    total_length: int
    proto_bytes: bytes

    @property
    def is_complete(self) -> bool:
        return self.data_offset == 0 and self.total_length == len(self.proto_bytes)


def parse_message(frame: bytes) -> Optional[GfdiMessage]:
    """Decode a COBS-unwrapped GFDI frame. Returns ``None`` if malformed."""
    if len(frame) < 4:
        return None
    message_type = struct.unpack("<H", frame[2:4])[0]
    return GfdiMessage(type=message_type, payload=frame[4:], raw=frame)


def parse_protobuf_frame(message: GfdiMessage) -> Optional[ProtobufFrame]:
    """Pull the protobuf header out of a PROTOBUF_REQUEST/RESPONSE message."""
    if len(message.payload) < 14:
        return None
    request_id, data_offset, total_length, proto_len = struct.unpack(
        "<HIII", message.payload[:14]
    )
    return ProtobufFrame(
        message_type=message.type,
        request_id=request_id,
        data_offset=data_offset,
        total_length=total_length,
        proto_bytes=message.payload[14 : 14 + proto_len],
    )


def parse_system_event(message: GfdiMessage) -> Optional["tuple[int, int]"]:
    """Pull ``(event_type, value)`` out of a SYSTEM_EVENT message."""
    if not message.payload:
        return None
    value = message.payload[1] if len(message.payload) > 1 else 0
    return message.payload[0], value


@dataclass(frozen=True)
class DeviceInformation:
    """The watch's self-description, from a DEVICE_INFORMATION message (5024).

    Real watches push this unprompted shortly after the GFDI channel opens. It
    is the only place the firmware version and the true model name are
    available — the BLE advert name is a marketing name, not either of those.

    Wire format (little-endian, strings length-prefixed with one byte of UTF-8
    length), ported from Gadgetbridge's ``DeviceInformationMessage``::

        protocol_version   (H)
        product_number     (H)
        unit_number        (I)
        software_version   (H)
        max_packet_size    (H)
        bluetooth_name     (pascal string)
        device_name        (pascal string)
        device_model       (pascal string)
    """

    protocol_version: int
    product_number: int
    unit_number: int
    software_version: int
    max_packet_size: int
    bluetooth_name: str
    device_name: str
    device_model: str

    @property
    def firmware(self) -> str:
        """Software version formatted the way Garmin displays it (e.g. 19.20)."""
        return f"{self.software_version // 100}.{self.software_version % 100:02d}"

    def __str__(self) -> str:
        return f"{self.device_model} ({self.device_name}) firmware {self.firmware}"


def _read_pascal_string(data: bytes, offset: int) -> "tuple[str, int]":
    """Read a one-byte-length-prefixed UTF-8 string; return it and the new offset."""
    if offset >= len(data):
        return "", offset
    size = data[offset]
    offset += 1
    raw = data[offset : offset + size]
    return raw.decode("utf-8", errors="replace"), offset + size


def parse_device_information(message: GfdiMessage) -> Optional[DeviceInformation]:
    """Decode a DEVICE_INFORMATION message. Returns ``None`` if truncated."""
    payload = message.payload
    if len(payload) < 12:
        return None

    protocol_version, product_number, unit_number, software_version, max_packet_size = (
        struct.unpack("<HHIHH", payload[:12])
    )
    bluetooth_name, offset = _read_pascal_string(payload, 12)
    device_name, offset = _read_pascal_string(payload, offset)
    device_model, offset = _read_pascal_string(payload, offset)

    return DeviceInformation(
        protocol_version=protocol_version,
        product_number=product_number,
        unit_number=unit_number,
        software_version=software_version,
        max_packet_size=max_packet_size,
        bluetooth_name=bluetooth_name,
        device_name=device_name,
        device_model=device_model,
    )
