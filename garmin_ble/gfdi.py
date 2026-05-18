import struct
from .crc import compute_crc

class GfdiMessageBuilder:
    @staticmethod
    def build_protobuf_ack(request_id: int, data_offset: int) -> bytes:
        """
        Builds a GFDI Status Message (Type 5000) specifically as an ACK for a Protobuf Request (Type 5043).
        """
        message_type = 5000
        ref_message_type = 5043
        status = 0 # ACK
        protobuf_chunk_status = 0 # KEPT
        protobuf_status_code = 0 # NO_ERROR
        
        # Pack the payload without length and CRC first
        # Format: msg_type (H), ref_msg_type (H), status (b), request_id (H), data_offset (I), chunk_status (b), status_code (b)
        payload = struct.pack('<HHbHIbb', 
            message_type,
            ref_message_type,
            status,
            request_id,
            data_offset,
            protobuf_chunk_status,
            protobuf_status_code
        )
        
        # Calculate packet size (2 bytes for size + length of payload)
        packet_size = 2 + len(payload)
        
        # Build the final buffer up to the CRC
        buffer = struct.pack('<H', packet_size) + payload
        
        # Calculate CRC over the whole buffer
        crc = compute_crc(buffer)
        
        # Append CRC
        return buffer + struct.pack('<H', crc)
