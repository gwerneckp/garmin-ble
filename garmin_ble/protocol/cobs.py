import time

class CobsCoDec:
    """
    Consistent Overhead Byte Stuffing (COBS) Encoder/Decoder for Garmin.
    """
    BUFFER_TIMEOUT_SEC = 1.5

    def __init__(self):
        self._buffer = bytearray()
        self._last_update = 0
        self._decoded_message = None

    def received_bytes(self, data: bytes):
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
        result = self._decoded_message
        self._decoded_message = None
        return result

    def _decode(self):
        if self._decoded_message is not None:
            return

        if len(self._buffer) < 4:
            return

        if self._buffer[-1] != 0x00:
            return

        if self._buffer[0] != 0x00:
            return

        frame = self._buffer[1:-1]
        decoded = bytearray()
        
        i = 0
        while i < len(frame):
            code = frame[i]
            i += 1
            
            if code == 0:
                break
                
            payload_size = code - 1
            decoded.extend(frame[i : i + payload_size])
            i += payload_size
            
            if code != 0xFF and i < len(frame):
                decoded.append(0x00)

        self._decoded_message = bytes(decoded)
        self._buffer.clear()

    @staticmethod
    def encode(data: bytes) -> bytes:
        encoded = bytearray()
        encoded.append(0x00)
        
        start_pos = 0
        last_byte_was_zero = False
        
        while start_pos < len(data):
            zero_index = start_pos
            while zero_index < len(data) and data[zero_index] != 0x00:
                zero_index += 1
                
            last_byte_was_zero = (zero_index < len(data))
            payload_size = zero_index - start_pos
            
            while payload_size >= 0xFE:
                encoded.append(0xFF)
                encoded.extend(data[start_pos : start_pos + 0xFE])
                payload_size -= 0xFE
                start_pos += 0xFE
                
            encoded.append(payload_size + 1)
            encoded.extend(data[start_pos : start_pos + payload_size])
            start_pos = zero_index + 1
            
        if last_byte_was_zero:
            encoded.append(0x01)
            
        encoded.append(0x00)
        return bytes(encoded)
