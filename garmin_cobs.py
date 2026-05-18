import struct
import time

class CobsCoDec:
    """
    Consistent Overhead Byte Stuffing (COBS) Encoder/Decoder for Garmin.
    
    This implementation matches Gadgetbridge's logic, which relies on 
    a leading AND a trailing 0x00 byte (the leading zero is specific to Garmin/Gadgetbridge).
    """
    BUFFER_TIMEOUT_SEC = 1.5

    def __init__(self):
        self._buffer = bytearray()
        self._last_update = 0
        self._decoded_message = None

    def received_bytes(self, data: bytes):
        """
        Accumulate received bytes in a local buffer, clear on timeout, and attempt to parse.
        """
        now = time.time()
        if (now - self._last_update) > self.BUFFER_TIMEOUT_SEC:
            self.reset()
        self._last_update = now

        self._buffer.extend(data)
        self._decode()

    def reset(self):
        self._decoded_message = None
        self._buffer.clear()

    def retrieve_message(self) -> bytes:
        """Returns the decoded message if one is ready, then clears it."""
        result = self._decoded_message
        self._decoded_message = None
        return result

    def _decode(self):
        if self._decoded_message is not None:
            # A packet is already waiting, unable to parse more until retrieved
            return

        if len(self._buffer) < 4:
            # Minimal payload length including padding
            return

        if self._buffer[-1] != 0x00:
            # No 0x00 at the end, hence no full packet
            return

        if self._buffer[0] != 0x00:
            # No 0x00 at the start
            return

        # We have a full frame (starts with 0, ends with 0)
        # Skip the leading 0 and the trailing 0
        frame = self._buffer[1:-1]
        decoded = bytearray()
        
        i = 0
        while i < len(frame):
            code = frame[i]
            i += 1
            
            if code == 0:
                # Should not happen in COBS unless it's the end, which we stripped
                break
                
            payload_size = code - 1
            decoded.extend(frame[i : i + payload_size])
            i += payload_size
            
            if code != 0xFF and i < len(frame):
                decoded.append(0x00)

        self._decoded_message = bytes(decoded)
        # Clear the buffer since we processed it
        # (Assuming 1 packet per buffer flush based on GB logic)
        self._buffer.clear()

    @staticmethod
    def encode(data: bytes) -> bytes:
        """
        Garmin variant of COBS encoding: adds leading 0x00 and trailing 0x00.
        """
        encoded = bytearray()
        encoded.append(0x00) # Garmin initial padding
        
        start_pos = 0
        last_byte_was_zero = False
        
        while start_pos < len(data):
            # Find the next zero byte
            zero_index = start_pos
            while zero_index < len(data) and data[zero_index] != 0x00:
                zero_index += 1
                
            last_byte_was_zero = (zero_index < len(data))
            
            payload_size = zero_index - start_pos
            
            while payload_size >= 0xFE:
                encoded.append(0xFF) # Max payload size indicator
                encoded.extend(data[start_pos : start_pos + 0xFE])
                payload_size -= 0xFE
                start_pos += 0xFE
                
            encoded.append(payload_size + 1)
            encoded.extend(data[start_pos : start_pos + payload_size])
            
            start_pos = zero_index + 1 # Skip the zero byte for the next block
            
        if last_byte_was_zero:
            encoded.append(0x01)
            
        encoded.append(0x00) # Trailing zero byte
        return bytes(encoded)

if __name__ == "__main__":
    # Simple test to verify the logic works symmetrically
    test_data = b"Hello\x00Garmin\x00World"
    print("Original:", test_data)
    
    encoded = CobsCoDec.encode(test_data)
    print("Encoded :", encoded)
    
    codec = CobsCoDec()
    codec.received_bytes(encoded)
    decoded = codec.retrieve_message()
    print("Decoded :", decoded)
    
    assert test_data == decoded, "COBS decode did not match original data"
    print("Test passed!")
