import pytest
from garmin_ble.parsers.garmin_json import GarminJson, GarminJsonException

def test_garmin_json_encode_decode_primitives():
    # Test Null
    encoded = GarminJson.encode(None)
    decoded = GarminJson.decode(encoded)
    assert decoded is None
    
    # Test Bool
    encoded = GarminJson.encode(True)
    decoded = GarminJson.decode(encoded)
    assert decoded is True
    
    encoded = GarminJson.encode(False)
    decoded = GarminJson.decode(encoded)
    assert decoded is False
    
    # Test int (sint32)
    encoded = GarminJson.encode(42)
    decoded = GarminJson.decode(encoded)
    assert decoded == 42
    
    # Test large int (sint64)
    large_int = 2147483648
    encoded = GarminJson.encode(large_int)
    decoded = GarminJson.decode(encoded)
    assert decoded == large_int
    
    # Test string
    encoded = GarminJson.encode("Hello, Garmin!")
    decoded = GarminJson.decode(encoded)
    assert decoded == "Hello, Garmin!"
    
    # Test float/double
    encoded = GarminJson.encode(3.14)
    decoded = GarminJson.decode(encoded)
    assert isinstance(decoded, float)
    assert abs(decoded - 3.14) < 1e-6

def test_garmin_json_encode_decode_complex():
    # Test List
    data = [1, 2, "three", False, None]
    encoded = GarminJson.encode(data)
    decoded = GarminJson.decode(encoded)
    assert decoded == data
    
    # Test Map
    data = {"key1": "value1", "key2": 42, "key3": True}
    encoded = GarminJson.encode(data)
    decoded = GarminJson.decode(encoded)
    assert decoded == data
    
    # Test Nested
    data = {
        "user": {
            "name": "Gabriel",
            "age": 30,
            "hobbies": ["coding", "running"]
        },
        "active": True
    }
    encoded = GarminJson.encode(data)
    decoded = GarminJson.decode(encoded)
    assert decoded == data

def test_garmin_json_invalid_magic():
    data = {"hello": "world"}
    encoded = bytearray(GarminJson.encode(data))
    
    # Corrupt the string section magic
    encoded[0] = 0x00
    with pytest.raises(GarminJsonException):
        GarminJson.decode(bytes(encoded))

def test_garmin_json_empty_collections():
    encoded = GarminJson.encode([])
    decoded = GarminJson.decode(encoded)
    assert decoded == []
    
    encoded = GarminJson.encode({})
    decoded = GarminJson.decode(encoded)
    assert decoded == {}
