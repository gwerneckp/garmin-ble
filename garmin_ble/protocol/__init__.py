"""Wire-protocol encoding and decoding, independent of any transport.

Nothing in this package touches BLE, asyncio, or the public API. Everything
here is a pure function over bytes, which is what makes the simulator and the
tests able to speak the protocol without a radio.
"""

from __future__ import annotations

from .cobs import CobsCoDec
from .crc import compute_crc
from .garmin_json import GarminJson, GarminJsonException
from .gfdi import (
    GfdiMessage,
    GfdiMessageBuilder,
    ProtobufFrame,
    parse_message,
    parse_protobuf_frame,
    parse_system_event,
)
from .mlr import (
    ControlMessage,
    MlrPacket,
    RegisterMlResponse,
    build_close_all_request,
    build_close_all_response,
    build_register_ml_request,
    build_register_ml_response,
    decode_packet,
    encode_rx,
    encode_tx,
)
from .smart import SmartRouter

__all__ = [
    "CobsCoDec",
    "compute_crc",
    "GarminJson",
    "GarminJsonException",
    "GfdiMessage",
    "GfdiMessageBuilder",
    "ProtobufFrame",
    "parse_message",
    "parse_protobuf_frame",
    "parse_system_event",
    "ControlMessage",
    "MlrPacket",
    "RegisterMlResponse",
    "build_close_all_request",
    "build_close_all_response",
    "build_register_ml_request",
    "build_register_ml_response",
    "decode_packet",
    "encode_rx",
    "encode_tx",
    "SmartRouter",
]
