"""Tests for COBS (Consistent Overhead Byte Stuffing) encoder/decoder.

CobsCoDec is a core protocol layer. The Garmin variant uses a leading
AND trailing 0x00 byte framing, which differs from standard COBS.
"""
import pytest
from garmin_ble.cobs import CobsCoDec


# ── Round-trip tests ─────────────────────────────────────────────

class TestCobsRoundTrip:
    """Encode data, then decode it — must produce the original."""

    @pytest.mark.parametrize("data", [
        b"\x00",
        b"Hello",
        b"Hello\x00World",
        b"\x00\x00\x00",
        b"\x01\x02\x03\xFF\xFE",
        bytes(range(256)),
        b"A" * 512,
        b"\x00" + b"A" * 254 + b"\x00",
        b"Garmin" + b"\x00" * 10 + b"BLE",
    ])
    def test_round_trip(self, data):
        encoded = CobsCoDec.encode(data)
        codec = CobsCoDec()
        codec.received_bytes(encoded)
        decoded = codec.retrieve_message()
        assert decoded == data

    def test_empty_data_encodes_but_does_not_decode(self):
        """Empty data encodes to \\x00\\x00 but decoder rejects frames < 4 bytes."""
        encoded = CobsCoDec.encode(b"")
        assert encoded == b"\x00\x00"
        codec = CobsCoDec()
        codec.received_bytes(encoded)
        assert codec.retrieve_message() is None

    def test_multiple_messages(self):
        """Process several messages through the same codec instance."""
        codec = CobsCoDec()
        messages = [b"msg1", b"\x00msg2\x00", b"msg3\x00msg4"]
        for msg in messages:
            encoded = CobsCoDec.encode(msg)
            codec.received_bytes(encoded)
            decoded = codec.retrieve_message()
            assert decoded == msg


# ── Encoding structure ───────────────────────────────────────────

class TestCobsEncoding:
    def test_starts_and_ends_with_zero(self):
        """Garmin framing: every encoded frame starts and ends with 0x00."""
        for data in [b"test", b"\x00", b"", b"A" * 100]:
            encoded = CobsCoDec.encode(data)
            assert encoded[0] == 0x00
            assert encoded[-1] == 0x00

    def test_single_non_zero_byte(self):
        encoded = CobsCoDec.encode(b"A")
        # 00 02 41 00
        assert encoded == b"\x00\x02\x41\x00"

    def test_all_zeros(self):
        encoded = CobsCoDec.encode(b"\x00\x00\x00")
        # 00 01 01 01 00
        assert encoded[0] == 0x00
        assert encoded[-1] == 0x00

    def test_max_chunk_size(self):
        """A chunk of 254 non-zero bytes uses 0xFF code with trailing 0x01."""
        data = b"A" * 254
        encoded = CobsCoDec.encode(data)
        # Structure: 00 FF <254 A's> 01 00
        assert len(encoded) == 1 + 1 + 254 + 1 + 1
        assert encoded[1] == 0xFF
        assert encoded[-2] == 0x01

    def test_overflow_chunk(self):
        """255 non-zero bytes needs two chunks: 0xFF(254) + 0x02(1)."""
        data = b"A" * 255
        encoded = CobsCoDec.encode(data)
        # 00 FF <254 A's> 02 <1 A> 00
        assert encoded[1] == 0xFF
        # At position 1 + 1 + 254 = 256, should be next code byte (0x02)
        assert encoded[1 + 1 + 254] == 0x02
        assert encoded[-1] == 0x00


# ── Edge cases ───────────────────────────────────────────────────

class TestCobsEdgeCases:
    def test_decode_incomplete_frame(self):
        """Frame without trailing 0x00 should not decode."""
        codec = CobsCoDec()
        partial = CobsCoDec.encode(b"hello")[:-1]
        codec.received_bytes(partial)
        assert codec.retrieve_message() is None

    def test_decode_too_short(self):
        """Fewer than 4 bytes cannot be a valid frame."""
        codec = CobsCoDec()
        for short in [b"", b"\x00", b"\x00\x01", b"\x00\x01\x02"]:
            codec.received_bytes(short)
            assert codec.retrieve_message() is None

    def test_buffer_timeout(self, mocker):
        """Stale buffer should reset after BUFFER_TIMEOUT_SEC."""
        codec = CobsCoDec()
        codec._buffer = bytearray(b"\x00\x02A\x00")
        codec._last_update = 0  # ancient

        mocker.patch("time.time", return_value=1000)  # far in future
        codec.received_bytes(b"\x00")  # trigger timeout check
        assert len(codec._buffer) == 1  # reset then extended

    def test_retrieve_message_clears(self):
        """retrieve_message should return the decoded message only once."""
        codec = CobsCoDec()
        encoded = CobsCoDec.encode(b"test")
        codec.received_bytes(encoded)
        first = codec.retrieve_message()
        second = codec.retrieve_message()
        assert first == b"test"
        assert second is None

    def test_reset(self):
        codec = CobsCoDec()
        codec._decoded_message = b"stale"
        codec._buffer = bytearray(b"\x00garbage\x00")
        codec.reset()
        assert codec.retrieve_message() is None
        assert len(codec._buffer) == 0
