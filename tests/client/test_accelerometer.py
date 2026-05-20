import asyncio
import math
import pytest
from garmin_ble.client.base import GarminClientBase

def get_parsed_samples(packet_hex: str):
    """Helper to parse a hex packet and return the samples list."""
    from garmin_ble.constants import GarminService
    client = GarminClientBase()
    received = []
    client.on("accel", lambda s: received.extend(s))
    raw = bytes.fromhex(packet_hex)
    handle = raw[0] & 0x7F
    client.service_handles[handle] = GarminService.REALTIME_ACCELEROMETER
    asyncio.run(client._notify_handler(None, raw))
    return received

def test_parse_accel_desk_up():
    """Test stationary flat on desk, face up. Z should be approx -1.0g."""
    # Prepend 0x10 MLR handle to the 16-byte raw payload
    packet_hex = "104f71000000010f00fd1ff002e0ff021f"
    samples = get_parsed_samples(packet_hex)
    
    assert len(samples) == 3
    s1 = samples[0]
    
    assert s1[0] == 0.0
    assert round(s1[2], 3) == -0.996
    
    # Magnitude should be approx 1g
    mag = math.sqrt(s1[0]**2 + s1[1]**2 + s1[2]**2)
    assert 0.95 < mag < 1.05

def test_parse_accel_desk_down():
    """Test stationary flat on desk, face down. Z should be approx +1.0g."""
    packet_hex = "10a16604d0ff015100fe0f1005f0fffe10"
    samples = get_parsed_samples(packet_hex)
    
    assert len(samples) == 3
    s1 = samples[0]
    
    # X and Y near 0, Z near +1.0
    assert abs(s1[0]) < 0.05
    assert abs(s1[1]) < 0.05
    assert round(s1[2], 3) == 1.004

    mag = math.sqrt(s1[0]**2 + s1[1]**2 + s1[2]**2)
    assert 0.95 < mag < 1.05

def test_parse_accel_wall_45():
    """Test tilted 45 degrees. Gravity should be split across X and Z axes."""
    packet_hex = "100669354f01694ff31470f6343f01671f"
    samples = get_parsed_samples(packet_hex)
    
    assert len(samples) == 3
    s1 = samples[0]
    
    # X is approx -0.79g, Z is approx -0.59g
    assert round(s1[0], 2) == -0.79
    assert round(s1[2], 2) == -0.59
    
    # Despite being split, total magnitude must still be 1g
    mag = math.sqrt(s1[0]**2 + s1[1]**2 + s1[2]**2)
    assert 0.95 < mag < 1.05

def test_parse_accel_shaking():
    """Test dynamic movement. Values should wildly exceed 1g."""
    packet_hex = "100b712dc307a51056fc09fe7583af6b10"
    samples = get_parsed_samples(packet_hex)
    
    assert len(samples) == 3
    
    # Check sample 1
    assert round(samples[0][0], 2) == 3.18
    assert round(samples[0][2], 2) == 0.64
    
    # Check sample 2 (very high forces)
    assert round(samples[1][0], 2) == 5.38
    assert round(samples[1][1], 2) == -6.02
    
    mag_s2 = math.sqrt(samples[1][0]**2 + samples[1][1]**2 + samples[1][2]**2)
    assert mag_s2 > 5.0 # Total force > 5g
