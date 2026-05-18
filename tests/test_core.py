def test_import():
    """Verify that the library can be imported correctly."""
    import garmin_ble
    from garmin_ble import GarminClient
    from garmin_ble.constants import GarminService

    assert GarminClient is not None
    assert GarminService.GFDI == 1

def test_cobs():
    """Verify the COBS encoder/decoder works properly."""
    from garmin_ble.cobs import CobsCoDec

    test_data = b"Hello\x00Garmin\x00World"
    encoded = CobsCoDec.encode(test_data)
    
    codec = CobsCoDec()
    codec.received_bytes(encoded)
    decoded = codec.retrieve_message()

    assert test_data == decoded
