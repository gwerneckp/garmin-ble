"""Tests for the Garmin CRC16 algorithm.

The CRC uses a custom 16-entry constant table (not the standard CRC-16-CCITT).
These expected values are computed by the actual algorithm to serve as
regression tests — they are NOT from an external reference.
"""
import struct
import pytest
from garmin_ble.crc import compute_crc


class TestCrcConstants:
    """The constant table must match Gadgetbridge's reference."""

    def test_table_values(self):
        from garmin_ble.crc import CONSTANTS
        expected = [
            0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
            0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400
        ]
        assert CONSTANTS == expected


class TestCrcComputation:
    """Verify CRC results — these are regression tests against known values."""

    @pytest.mark.parametrize("data, expected", [
        (b"", 0x0000),
        (b"\x00", 0x0000),
        (b"A", 12480),
        (b"\xFF", 16448),
        (b"Hello", 62291),
        (b"\x00" * 10, 0x0000),
        (bytes(range(256)), 0xBAD3),
    ])
    def test_known_values(self, data, expected):
        assert compute_crc(data) == expected

    def test_initial_crc(self):
        """Verify CRC chaining with a non-zero initial value."""
        crc1 = compute_crc(b"Hello")
        crc2 = compute_crc(b" World", initial_crc=crc1)
        crc_full = compute_crc(b"Hello World")
        assert crc_full == crc2

    def test_incremental_vs_single(self):
        """Compute CRC in chunks matches compute once."""
        data = b"The quick brown fox jumps over the lazy dog"
        full = compute_crc(data)
        incremental = compute_crc(b"The quick ", initial_crc=0)
        incremental = compute_crc(b"brown fox ", initial_crc=incremental)
        incremental = compute_crc(b"jumps over the lazy dog", initial_crc=incremental)
        assert full == incremental


class TestCrcGfdiStructure:
    """Verify CRC fits into GFDI message structure correctly."""

    def test_crc_is_16_bit(self):
        """CRC must fit in an unsigned 16-bit integer."""
        for data in [b"", b"test", b"\xFF" * 100, bytes(range(256))]:
            crc = compute_crc(data)
            assert 0 <= crc <= 0xFFFF

    def test_gfdi_ack_crc(self):
        """Recreate the exact CRC from GfdiMessageBuilder.build_protobuf_ack."""
        from garmin_ble.gfdi import GfdiMessageBuilder
        msg = GfdiMessageBuilder.build_protobuf_ack(ref_msg_type=5043, request_id=0, data_offset=0)
        # Last 2 bytes are CRC little-endian
        crc_from_msg = struct.unpack('<H', msg[-2:])[0]
        recomputed = compute_crc(msg[:-2])
        assert crc_from_msg == recomputed
