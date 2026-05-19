import struct
import pytest
from unittest.mock import AsyncMock, MagicMock, PropertyMock


@pytest.fixture
def mock_bleak_client():
    """Create a fully mocked BleakClient with common defaults."""
    client = MagicMock()
    client.address = "00:00:00:00:00:00"
    client.is_connected = True
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.start_notify = AsyncMock()
    client.write_gatt_char = AsyncMock()
    client.services = MagicMock()
    # Default MTU: return 0 or raise AttributeError to test fallback
    type(client).mtu_size = PropertyMock(return_value=0)
    return client


@pytest.fixture
def mock_bleak_scanner(mocker):
    """Mock BleakScanner.discover to return a Garmin device."""
    mock_device = MagicMock()
    mock_device.name = "fenix 7"
    mock_device.address = "AA:BB:CC:DD:EE:FF"

    mock_adv = MagicMock()
    mock_adv.local_name = "fenix 7"

    mocker.patch(
        "garmin_ble.client.auto.BleakScanner.discover",
        new=AsyncMock(return_value={mock_device.address: (mock_device, mock_adv)})
    )
    return mock_device


def make_mlr_packet(handle: int, payload: bytes) -> bytes:
    """
    Build an MLR (Multi-Link Routing) frame.
    Bit 7 set = MLR, bits 6:4 = handle.
    """
    header = 0x80 | (handle << 4)
    return bytes([header]) + payload


def make_gatt_char(uuid_str: str) -> MagicMock:
    """Create a mock BLE GATT characteristic with the given UUID."""
    char = MagicMock()
    char.uuid = uuid_str
    return char


def make_gatt_service(start: int = 0x2810, count: int = 5) -> MagicMock:
    """Create a mock BLE service with sequential Garmin-style characteristics."""
    service = MagicMock()
    chars = [make_gatt_char(f"6a4e{i:04x}-667b-11e3-949a-0800200c9a66")
             for i in range(start, start + count)]
    service.characteristics = chars
    return service


def make_mock_services_with_rx_tx():
    """Build a mock services list containing a Garmin RX/TX service (0x281x)."""
    return [make_gatt_service(0x2810, 5), make_gatt_service(0x2820, 5)]


def make_control_response(msg_type: int, *fields) -> bytes:
    """
    Build a control handle (0x00) response.

    Minimal 14-byte control frame: type(1) + data.
    For CLOSE_ALL_RESP (type=6): minimal frame is 2 bytes.
    For REGISTER_ML_RESP (type=1): needs service_code, status, assigned_handle.
    """
    data = bytearray([0, msg_type])
    for f in fields:
        if isinstance(f, int):
            data.extend(struct.pack('<H', f) if f > 255 else bytes([f]))
        elif isinstance(f, bytes):
            data.extend(f)
    # Pad to at least 14 bytes for REGISTER_ML_RESP
    while len(data) < 14:
        data.append(0)
    return bytes(data)
