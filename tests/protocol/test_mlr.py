"""MLR framing and the control channel.

The host and the watch frame the same channel differently — the host writes a
bare handle byte, the watch sets the MLR flag — and getting that backwards
produces a handshake that silently never completes. These tests pin both
directions.
"""

import struct

import pytest

from garmin_ble.constants import CLIENT_ID, GarminService, RequestType
from garmin_ble.protocol import mlr


class TestFraming:
    def test_host_writes_a_bare_handle_byte(self):
        """Only the watch sets the MLR flag; the host must not."""
        assert mlr.encode_tx(3, b"\x01") == bytes([0x03, 0x01])

    def test_watch_sets_the_mlr_flag_for_low_handles(self):
        assert mlr.encode_rx(3, b"\x01") == bytes([0x80 | (3 << 4), 0x01])

    def test_watch_falls_back_to_a_bare_byte_above_seven(self):
        """The flagged form has only three bits of handle."""
        assert mlr.encode_rx(9, b"\x01") == bytes([0x09, 0x01])

    @pytest.mark.parametrize("handle", range(8))
    def test_flagged_frames_round_trip(self, handle):
        decoded = mlr.decode_packet(mlr.encode_rx(handle, b"\xaa\xbb"))
        assert isinstance(decoded, mlr.MlrPacket)
        assert decoded.handle == handle

    @pytest.mark.parametrize("handle", [8, 9, 15, 127])
    def test_high_handles_round_trip(self, handle):
        decoded = mlr.decode_packet(mlr.encode_rx(handle, b"\xaa"))
        assert isinstance(decoded, mlr.MlrPacket)
        assert decoded.handle == handle

    @pytest.mark.parametrize("handle", [0x80, 0xC8, 256, -1])
    def test_unaddressable_handles_are_rejected(self, handle):
        """A bare byte of 0x80+ sets the MLR flag and decodes as another handle.

        Refusing to encode it is the only safe answer: silently emitting one
        would route the packet to the wrong service.
        """
        with pytest.raises(ValueError):
            mlr.encode_tx(handle, b"")
        with pytest.raises(ValueError):
            mlr.encode_rx(handle, b"")

    def test_payload_keeps_the_routing_byte(self):
        """Parsers slice it off themselves, so it must still be present."""
        decoded = mlr.decode_packet(mlr.encode_rx(2, b"\xaa\xbb"))
        assert decoded.payload[1:] == b"\xaa\xbb"


class TestDecoding:
    def test_empty_input_is_not_a_packet(self):
        assert mlr.decode_packet(b"") is None

    def test_truncated_control_frame_is_not_a_packet(self):
        assert mlr.decode_packet(b"\x00") is None

    def test_close_all_response(self):
        decoded = mlr.decode_packet(
            mlr.encode_tx(mlr.CONTROL_HANDLE, mlr.build_close_all_response())
        )
        assert isinstance(decoded, mlr.ControlMessage)
        assert decoded.type == RequestType.CLOSE_ALL_RESP

    def test_register_ml_response(self):
        packet = mlr.encode_tx(
            mlr.CONTROL_HANDLE, mlr.build_register_ml_response(GarminService.REALTIME_HR, 5)
        )
        decoded = mlr.decode_packet(packet)
        assert isinstance(decoded, mlr.RegisterMlResponse)
        assert decoded.service == GarminService.REALTIME_HR
        assert decoded.handle == 5
        assert decoded.accepted

    def test_rejected_registration_is_flagged(self):
        """A non-zero status is a refusal, and the handle must not be used."""
        packet = mlr.encode_tx(
            mlr.CONTROL_HANDLE,
            mlr.build_register_ml_response(GarminService.REALTIME_SPO2, 0, status=1),
        )
        decoded = mlr.decode_packet(packet)
        assert not decoded.accepted

    def test_truncated_registration_response_is_rejected(self):
        assert mlr.decode_packet(bytes([0, RequestType.REGISTER_ML_RESP, 0, 0])) is None


class TestControlRequests:
    """The byte layout the watch expects; these offsets are not negotiable."""

    def test_close_all_request(self):
        packet = mlr.encode_tx(mlr.CONTROL_HANDLE, mlr.build_close_all_request())
        assert len(packet) == 13
        assert packet[0] == 0  # control handle
        assert packet[1] == RequestType.CLOSE_ALL_REQ

    def test_register_ml_request_puts_the_service_at_offset_10(self):
        packet = mlr.encode_tx(
            mlr.CONTROL_HANDLE, mlr.build_register_ml_request(GarminService.REALTIME_HR)
        )
        assert len(packet) == 13
        assert packet[1] == RequestType.REGISTER_ML_REQ
        assert struct.unpack("<q", packet[2:10])[0] == CLIENT_ID
        assert struct.unpack("<h", packet[10:12])[0] == GarminService.REALTIME_HR

    def test_request_and_response_agree_on_the_service_offset(self):
        """The round trip that the handshake actually depends on."""
        request = mlr.encode_tx(mlr.CONTROL_HANDLE, mlr.build_register_ml_request(19))
        service = struct.unpack("<h", request[10:12])[0]
        response = mlr.encode_tx(
            mlr.CONTROL_HANDLE, mlr.build_register_ml_response(service, 4)
        )
        assert mlr.decode_packet(response).service == 19


class TestFragmentation:
    def test_chunks_leave_room_for_the_header(self):
        chunks = mlr.fragment(bytes(100), max_chunk=21)
        assert all(len(c) <= 20 for c in chunks)
        assert b"".join(chunks) == bytes(100)

    def test_small_payload_is_one_chunk(self):
        assert mlr.fragment(b"abc", max_chunk=20) == [b"abc"]

    def test_empty_payload_still_yields_one_chunk(self):
        assert mlr.fragment(b"", max_chunk=20) == [b""]

    def test_exact_multiple_does_not_emit_a_trailing_empty_chunk(self):
        chunks = mlr.fragment(bytes(40), max_chunk=21)
        assert chunks == [bytes(20), bytes(20)]
