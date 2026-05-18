CONSTANTS = [
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400
]

def compute_crc(data: bytes, initial_crc: int = 0) -> int:
    """
    Garmin CRC16 algorithm.
    """
    crc = initial_crc
    for b in data:
        crc = (((crc >> 4) & 4095) ^ CONSTANTS[crc & 15]) ^ CONSTANTS[b & 15]
        crc = (((crc >> 4) & 4095) ^ CONSTANTS[crc & 15]) ^ CONSTANTS[(b >> 4) & 15]
    return crc
