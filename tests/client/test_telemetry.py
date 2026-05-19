"""Tests for real-time telemetry data parsing from Garmin watches.

Each sensor type (HR, steps, HRV, SpO2, respiration, accelerometer) has a dedicated
MLR handle assigned during the handshake.  Telemetry arrives as small
MLR-framed packets with sensor-specific binary payloads.
"""
import struct
import pytest
from unittest.mock import AsyncMock, MagicMock, PropertyMock

from garmin_ble.client import GarminClientBase
from garmin_ble.constants import GarminService


@pytest.fixture
async def connected_client(mock_bleak_scanner, mock_bleak_client, mocker):
    """Return a GarminClientBase that has completed the connect() handshake."""
    mocker.patch("garmin_ble.client.base.BleakClient", return_value=mock_bleak_client)
    from tests.conftest import make_mock_services_with_rx_tx
    mock_bleak_client.services = make_mock_services_with_rx_tx()
    mock_bleak_client.write_gatt_char = AsyncMock()

    client = GarminClientBase()
    await client.connect("AA:BB:CC:DD:EE:FF")
    notify_handler = mock_bleak_client.start_notify.call_args[0][1]
    return client, notify_handler


@pytest.fixture
async def client_with_handles(connected_client):
    """Connected client with pre-populated service_handle mappings."""
    client, notify_handler = connected_client
    client.service_handles = {
        0x01: GarminService.GFDI,
        0x02: GarminService.REALTIME_HR,
        0x03: GarminService.REALTIME_STEPS,
        0x04: GarminService.REALTIME_HRV,
        0x05: GarminService.REALTIME_SPO2,
        0x06: GarminService.REALTIME_RESPIRATION,
        0x07: GarminService.REALTIME_ACCELEROMETER,
    }
    return client, notify_handler


class TestTelemetryParsing:
    """We are testing if: each sensor's MLR packets are parsed into correct events.

    MLR header byte 0: bit 7 set (MLR flag), bits 6:4 = handle nibble,
    byte 1: padding.  Payload format varies per sensor type.
    """

    async def test_heart_rate(self, client_with_handles):
        """We are testing if: an HR packet (handle 0x02) fires the 'hr' event correctly.

        MLR packet bytes: [0x80 | (0x02 << 4), padding, hr_value, resting_hr].
        The callback receives (hr, resting_hr).
        """
        client, notify = client_with_handles
        results = []
        client.on("hr", lambda hr, rest: results.append((hr, rest)))

        packet = bytes([0x80 | (0x02 << 4), 0, 72, 55])
        await notify(None, packet)
        assert results == [(72, 55)]

    async def test_steps(self, client_with_handles):
        """We are testing if: a steps packet (handle 0x03) fires the 'steps' event.

        Payload is two little-endian uint32: steps_today, goal.
        """
        client, notify = client_with_handles
        results = []
        client.on("steps", lambda s, g: results.append((s, g)))

        packet = bytes([0x80 | (0x03 << 4)]) + struct.pack('<II', 5432, 10000)
        await notify(None, packet)
        assert results == [(5432, 10000)]

    async def test_hrv(self, client_with_handles):
        """We are testing if: an HRV packet (handle 0x04) fires the 'hrv' event.

        Payload is a little-endian uint16 milliseconds RR-interval.
        """
        client, notify = client_with_handles
        results = []
        client.on("hrv", lambda rr: results.append(rr))

        packet = bytes([0x80 | (0x04 << 4)]) + struct.pack('<H', 850)
        await notify(None, packet)
        assert results == [850]

    async def test_spo2(self, client_with_handles):
        """We are testing if: an SpO2 packet (handle 0x05) fires the 'spo2' event.

        Payload is a single byte: percentage (0-100).  255 = invalid.
        """
        client, notify = client_with_handles
        results = []
        client.on("spo2", lambda s: results.append(s))

        packet = bytes([0x80 | (0x05 << 4), 98])
        await notify(None, packet)
        assert results == [98]

    async def test_spo2_invalid_ignored(self, client_with_handles):
        """We are testing if: SpO2 value 255 is treated as invalid and ignored.

        Value 0xFF is Garmin's "sensor not ready" sentinel — must not fire event.
        """
        client, notify = client_with_handles
        results = []
        client.on("spo2", lambda s: results.append(s))

        packet = bytes([0x80 | (0x05 << 4), 255])
        await notify(None, packet)
        assert results == []

    async def test_respiration(self, client_with_handles):
        """We are testing if: a respiration packet (handle 0x06) fires correctly.

        Payload is a single byte: breaths per minute (1-127).  0 = invalid.
        """
        client, notify = client_with_handles
        results = []
        client.on("respiration", lambda b: results.append(b))

        packet = bytes([0x80 | (0x06 << 4), 14])
        await notify(None, packet)
        assert results == [14]

    async def test_respiration_zero_ignored(self, client_with_handles):
        """We are testing if: respiration value 0 is treated as invalid.

        A watch with no valid respiration data sends 0 — must not fire event.
        """
        client, notify = client_with_handles
        results = []
        client.on("respiration", lambda b: results.append(b))

        packet = bytes([0x80 | (0x06 << 4), 0])
        await notify(None, packet)
        assert results == []

    async def test_multiple_telemetry_streams(self, client_with_handles):
        """We are testing if: interleaved packets across handles all route to correct callbacks.

        Simulates real-world watch behavior: all sensors reporting near-simultaneously.
        """
        client, notify = client_with_handles
        hr_res, step_res, hrv_res, spo2_res, resp_res = [], [], [], [], []
        client.on("hr", lambda hr, rest: hr_res.append((hr, rest)))
        client.on("steps", lambda s, g: step_res.append((s, g)))
        client.on("hrv", lambda rr: hrv_res.append(rr))
        client.on("spo2", lambda s: spo2_res.append(s))
        client.on("respiration", lambda b: resp_res.append(b))

        packets = [
            bytes([0x80 | (0x02 << 4), 0, 75, 50]),
            bytes([0x80 | (0x03 << 4)]) + struct.pack('<II', 1000, 8000),
            bytes([0x80 | (0x04 << 4)]) + struct.pack('<H', 720),
            bytes([0x80 | (0x05 << 4), 97]),
            bytes([0x80 | (0x06 << 4), 16]),
        ]
        for pkt in packets:
            await notify(None, pkt)

        assert hr_res == [(75, 50)]
        assert step_res == [(1000, 8000)]
        assert hrv_res == [720]
        assert spo2_res == [97]
        assert resp_res == [16]

    async def test_accelerometer(self, client_with_handles):
        """We are testing if: accelerometer packets are parsed into structured dicts.

        Uses real captured data from a Garmin watch. Each 16-byte packet
        (after the service header) decodes to 10× 12-bit values:
          3 XYZ samples + 1 timestamp.
        """
        client, notify = client_with_handles
        results = []
        client.on("accel", lambda p: results.append(p))

        # Real captured packets (raw BLE, including the MLR header byte)
        packets = [
            bytes([0x80 | (0x07 << 4)]) + bytearray(b'Rl\xaa\xaf\x02\x14\xcf\xfa- \xf1\xb2/\x02\x0e\x1f'),
            bytes([0x80 | (0x07 << 4)]) + bytearray(b'\xc5l\xb1\x8f\x02\x17?\xfb"\xa0\xf1\xb1\xdf\x02\x17\x1f'),
            bytes([0x80 | (0x07 << 4)]) + bytearray(b'9m\xb1\xdf\x01\t\x8f\xfa-\xe0\xf0\xa9\xcf\x02\x19\x1f'),
        ]

        for pkt in packets:
            await notify(None, pkt)

        assert len(results) == 3

        # --- Packet 1: ts=3154ms ---
        p0 = results[0]
        assert p0["timestamp_ms"] == 3154
        assert p0["samples"][0] == (-1370, 687, 320)
        assert p0["samples"][1] == (-1329, 735, 288)
        assert p0["samples"][2] == (-1233, 559, 224)

        # --- Packet 2: ts=3269ms ---
        p1 = results[1]
        assert p1["timestamp_ms"] == 3269
        assert p1["samples"][0] == (-1258, 655, 368)
        assert p1["samples"][1] == (-1217, 559, 416)
        assert p1["samples"][2] == (-1249, 735, 368)

        # --- Packet 3: ts=3385ms ---
        p2 = results[2]
        assert p2["timestamp_ms"] == 3385
        assert p2["samples"][0] == (-1258, 479, 144)
        assert p2["samples"][1] == (-1393, 735, 224)
        assert p2["samples"][2] == (-1377, 719, 400)
