"""MLR (Multi-Link Routing) framing and the control channel.

Garmin multiplexes several logical services over one BLE characteristic pair.
The first byte routes the packet:

* bit 7 set  → an MLR data frame; bits 6:4 are the handle.
* bit 7 clear and byte 0 == 0 → the control channel, where handles are
  negotiated.

Handles are assigned at runtime by the watch, so the host must ask for one per
service (``REGISTER_ML_REQ``) and remember the mapping. ``CLOSE_ALL_REQ`` wipes
any assignments left over from a previous session — including one belonging to
a phone that just walked away.

These functions are the whole story for that channel, in both directions: the
host uses the ``request`` builders and the decoder, the simulator uses the
``response`` builders and the same decoder.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Union

from ..constants import CLIENT_ID, RequestType

#: Control-channel packets go out on handle 0.
CONTROL_HANDLE = 0

_MLR_FLAG = 0x80
_HANDLE_MASK = 0x70
_HANDLE_SHIFT = 4

#: Widest handle the 3-bit MLR field can express. Handles above this are
#: addressed by a bare handle byte instead, with the flag clear.
MAX_FLAGGED_HANDLE = 7

#: Widest handle expressible at all. A bare handle byte of 0x80 or more would
#: set the MLR flag and be read back as a flagged frame on a different handle,
#: so the routing byte can only carry handles up to 0x7F.
MAX_HANDLE = 0x7F


def _check_handle(handle: int) -> None:
    if not 0 <= handle <= MAX_HANDLE:
        raise ValueError(
            f"handle must be 0-{MAX_HANDLE} to be addressable in the routing "
            f"byte, got {handle}"
        )


def encode_tx(handle: int, payload: bytes) -> bytes:
    """Frame *payload* for the host-to-watch direction.

    The host addresses a handle with a bare handle byte; only the watch sets the
    MLR flag on the packets it sends back.
    """
    _check_handle(handle)
    return bytes([handle]) + payload


def encode_rx(handle: int, payload: bytes) -> bytes:
    """Frame *payload* the way a watch sends it. Used by the simulator.

    Handles 0-7 fit the 3-bit flagged form that real watches use; higher ones
    fall back to a bare handle byte, which the decoder also accepts.
    """
    _check_handle(handle)
    if handle <= MAX_FLAGGED_HANDLE:
        return bytes([_MLR_FLAG | (handle << _HANDLE_SHIFT)]) + payload
    return bytes([handle]) + payload


# ── decoded packet types ────────────────────────────────────────────────────


@dataclass(frozen=True)
class MlrPacket:
    """A data frame carrying service payload on an assigned handle."""

    handle: int
    payload: bytes


@dataclass(frozen=True)
class ControlMessage:
    """A control-channel frame that needs no fields beyond its type."""

    type: int


@dataclass(frozen=True)
class RegisterMlResponse:
    """The watch's answer to a handle request.

    ``status`` is 0 on success; any other value is a refusal, which callers
    must treat as such — the handle in a refused response is meaningless.
    """

    service: int
    status: int
    handle: int

    @property
    def accepted(self) -> bool:
        return self.status == 0


Decoded = Union[MlrPacket, ControlMessage, RegisterMlResponse, None]


def decode_packet(data: bytes) -> Decoded:
    """Route one raw notification into its decoded form.

    Returns ``None`` for input that is empty or too short to interpret, so the
    caller can count it as malformed rather than crash.
    """
    if not data:
        return None

    if data[0] & _MLR_FLAG:
        handle = (data[0] & _HANDLE_MASK) >> _HANDLE_SHIFT
        return MlrPacket(handle=handle, payload=data)

    if data[0] != CONTROL_HANDLE or len(data) < 2:
        # An unflagged, non-zero first byte is the watch addressing a handle
        # directly — the form it must use for handles above MAX_FLAGGED_HANDLE.
        return MlrPacket(handle=data[0], payload=data) if len(data) >= 2 else None

    msg_type = data[1]

    if msg_type == RequestType.REGISTER_ML_RESP:
        if len(data) < 14:
            return None
        service = struct.unpack("<h", data[10:12])[0]
        return RegisterMlResponse(service=service, status=data[12], handle=data[13])

    return ControlMessage(type=msg_type)


# ── builders: host side ─────────────────────────────────────────────────────


#
# Every builder below returns the payload *after* the routing byte, so that
# framing happens in exactly one place (:func:`encode_tx` / :func:`encode_rx`).
# Control-channel traffic is never MLR-flagged in either direction, so both
# sides frame it with ``encode_tx(CONTROL_HANDLE, ...)``.


def build_close_all_request() -> bytes:
    """Ask the watch to drop every existing handle assignment."""
    return struct.pack("<bhqb", RequestType.CLOSE_ALL_REQ, 0, CLIENT_ID, 0)


def build_register_ml_request(service_code: int) -> bytes:
    """Ask the watch to assign a handle for *service_code*."""
    return struct.pack("<bqhb", RequestType.REGISTER_ML_REQ, CLIENT_ID, service_code, 0)


# ── builders: watch side (used by the simulator and by tests) ───────────────


def build_close_all_response() -> bytes:
    return bytes([RequestType.CLOSE_ALL_RESP])


def build_register_ml_response(service_code: int, handle: int, status: int = 0) -> bytes:
    """Build the REGISTER_ML_RESP payload the watch sends back."""
    payload = bytearray(13)
    payload[0] = RequestType.REGISTER_ML_RESP
    payload[9:11] = struct.pack("<h", int(service_code))
    payload[11] = status
    payload[12] = handle
    return bytes(payload)


def fragment(payload: bytes, max_chunk: int) -> "list[bytes]":
    """Split *payload* into chunks that fit one BLE write, header included.

    ``max_chunk`` is the largest total write; one byte of every write is the MLR
    header, so the usable body is one less.
    """
    body = max(max_chunk - 1, 1)
    return [payload[i : i + body] for i in range(0, len(payload), body)] or [b""]


__all__ = [
    "CONTROL_HANDLE",
    "MAX_FLAGGED_HANDLE",
    "MAX_HANDLE",
    "encode_tx",
    "encode_rx",
    "decode_packet",
    "MlrPacket",
    "ControlMessage",
    "RegisterMlResponse",
    "build_close_all_request",
    "build_register_ml_request",
    "build_close_all_response",
    "build_register_ml_response",
    "fragment",
]
