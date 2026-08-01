import struct
from collections import deque
from typing import Any, Dict, List, Set, Tuple

class GarminJsonException(Exception):
    pass

class GarminJson:
    STRING_SECTION_MAGIC = b"\xab\xcd\xab\xcd"
    DATA_SECTION_MAGIC = b"\xda\x7a\xda\x7a"

    TYPE_NULL = 0x00
    TYPE_SINT32 = 0x01
    TYPE_FLOAT = 0x02
    TYPE_STRING = 0x03
    TYPE_ARRAY = 0x05
    TYPE_BOOL = 0x09
    TYPE_MAP = 0x0B
    TYPE_SINT64 = 0x0E
    TYPE_DOUBLE = 0x0F

    @classmethod
    def encode(cls, obj: Any) -> bytes:
        """Encode a Python object (dict, list, primitive) to Garmin binary JSON."""
        # First pass: collect all strings
        strings = []
        seen_strings = set()
        
        def _collect_strings(root_obj):
            queue = deque([root_obj])
            while queue:
                current = queue.popleft()
                if isinstance(current, dict):
                    for k, v in current.items():
                        if isinstance(k, str) and k not in seen_strings:
                            seen_strings.add(k)
                            strings.append(k)
                        if isinstance(v, str):
                            if v not in seen_strings:
                                seen_strings.add(v)
                                strings.append(v)
                        elif isinstance(v, (dict, list)):
                            queue.append(v)
                elif isinstance(current, list):
                    for v in current:
                        if isinstance(v, str):
                            if v not in seen_strings:
                                seen_strings.add(v)
                                strings.append(v)
                        elif isinstance(v, (dict, list)):
                            queue.append(v)
                elif isinstance(current, str):
                    if current not in seen_strings:
                        seen_strings.add(current)
                        strings.append(current)
        
        _collect_strings(obj)

        # Build string section
        string_section = bytearray()
        string_offsets = {}
        current_offset = 0
        for s in strings:
            string_offsets[s] = current_offset
            s_bytes = s.encode('utf-8')
            length = len(s_bytes) + 1  # +1 for null terminator
            string_section.extend(struct.pack('>H', length))
            string_section.extend(s_bytes)
            string_section.append(0x00)
            current_offset += 2 + len(s_bytes) + 1

        # Build data section (breadth-first)
        data_section = bytearray()
        queue = deque([obj])
        
        while queue:
            current = queue.popleft()
            
            if current is None:
                data_section.append(cls.TYPE_NULL)
            elif isinstance(current, bool):
                data_section.append(cls.TYPE_BOOL)
                data_section.append(0x01 if current else 0x00)
            elif isinstance(current, int):
                # Fit in 32-bit signed int?
                if -2147483648 <= current <= 2147483647:
                    data_section.append(cls.TYPE_SINT32)
                    data_section.extend(struct.pack('>i', current))
                else:
                    data_section.append(cls.TYPE_SINT64)
                    data_section.extend(struct.pack('>q', current))
            elif isinstance(current, float):
                # Since Python doesn't have a single precision float built-in type,
                # we will default to double precision unless it's explicitly asked for
                # But to match Java logic which checks if double value equals float value:
                # If we pack and unpack as float and it matches, we can use float
                # For simplicity, Garmin Java often uses double for floats, but we will pack as float if it doesn't lose precision.
                # Just use double to be safe and avoid precision loss.
                f32_val = struct.unpack('>f', struct.pack('>f', current))[0]
                if f32_val == current:
                    data_section.append(cls.TYPE_FLOAT)
                    data_section.extend(struct.pack('>f', current))
                else:
                    data_section.append(cls.TYPE_DOUBLE)
                    data_section.extend(struct.pack('>d', current))
            elif isinstance(current, str):
                data_section.append(cls.TYPE_STRING)
                offset = string_offsets.get(current)
                if offset is None:
                    raise GarminJsonException(f"String not found in offset map: {current}")
                data_section.extend(struct.pack('>I', offset))
            elif isinstance(current, list):
                data_section.append(cls.TYPE_ARRAY)
                data_section.extend(struct.pack('>I', len(current)))
                queue.extend(current)
            elif isinstance(current, dict):
                data_section.append(cls.TYPE_MAP)
                data_section.extend(struct.pack('>I', len(current)))
                for k, v in current.items():
                    queue.append(k)
                    queue.append(v)
            else:
                raise GarminJsonException(f"Unsupported type: {type(current)}")

        output = bytearray()
        if string_section:
            output.extend(cls.STRING_SECTION_MAGIC)
            output.extend(struct.pack('>I', len(string_section)))
            output.extend(string_section)
            
        output.extend(cls.DATA_SECTION_MAGIC)
        output.extend(struct.pack('>I', len(data_section)))
        output.extend(data_section)
        
        return bytes(output)

    @classmethod
    def decode(cls, data: bytes) -> Any:
        """Decode Garmin binary JSON to a Python object."""
        if len(data) < 9:
            raise GarminJsonException("Not enough bytes for GarminJson")
            
        offset = 0
        strings = {}
        
        # Parse string section if present
        magic = data[offset:offset+4]
        offset += 4
        
        if magic == cls.STRING_SECTION_MAGIC:
            string_section_len = struct.unpack('>I', data[offset:offset+4])[0]
            offset += 4
            string_section_end = offset + string_section_len
            string_section_start = offset
            
            while offset < string_section_end:
                str_start_offset = offset - string_section_start
                str_len = struct.unpack('>H', data[offset:offset+2])[0]
                offset += 2
                
                # Length includes null terminator
                str_bytes = data[offset:offset + str_len - 1]
                offset += str_len
                
                strings[str_start_offset] = str_bytes.decode('utf-8')
                
            magic = data[offset:offset+4]
            offset += 4
            
        if magic != cls.DATA_SECTION_MAGIC:
            raise GarminJsonException(f"Expected data section magic, got {magic}")
            
        data_section_len = struct.unpack('>I', data[offset:offset+4])[0]
        offset += 4
        
        if offset + data_section_len > len(data):
            raise GarminJsonException(f"Not enough bytes to decode data section")
            
        def _decode_value() -> Tuple[Any, bool]:
            nonlocal offset
            v_type = data[offset]
            offset += 1
            
            if v_type == cls.TYPE_NULL:
                return None, False
            elif v_type == cls.TYPE_BOOL:
                val = data[offset] != 0
                offset += 1
                return val, False
            elif v_type == cls.TYPE_SINT32:
                val = struct.unpack('>i', data[offset:offset+4])[0]
                offset += 4
                return val, False
            elif v_type == cls.TYPE_SINT64:
                val = struct.unpack('>q', data[offset:offset+8])[0]
                offset += 8
                return val, False
            elif v_type == cls.TYPE_FLOAT:
                val = struct.unpack('>f', data[offset:offset+4])[0]
                offset += 4
                return val, False
            elif v_type == cls.TYPE_DOUBLE:
                val = struct.unpack('>d', data[offset:offset+8])[0]
                offset += 8
                return val, False
            elif v_type == cls.TYPE_STRING:
                str_offset = struct.unpack('>I', data[offset:offset+4])[0]
                offset += 4
                if str_offset not in strings:
                    raise GarminJsonException(f"String not found at offset: {str_offset}")
                return strings[str_offset], False
            elif v_type == cls.TYPE_ARRAY:
                length = struct.unpack('>I', data[offset:offset+4])[0]
                offset += 4
                return {'_array': True, 'len': length, 'items': []}, True
            elif v_type == cls.TYPE_MAP:
                size = struct.unpack('>I', data[offset:offset+4])[0]
                offset += 4
                return {'_map': True, 'size': size, 'items': {}}, True
            else:
                raise GarminJsonException(f"Unknown type: 0x{v_type:02x}")

        root_obj, is_placeholder = _decode_value()
        
        queue = deque([root_obj])
        
        while queue:
            current = queue.popleft()
            
            if isinstance(current, dict) and current.get('_map'):
                for _ in range(current['size']):
                    k_val, k_ph = _decode_value()
                    v_val, v_ph = _decode_value()
                    
                    if k_ph or v_ph:
                        # Add to queue to resolve children
                        if v_ph:
                            queue.append(v_val)
                    
                    current['items'][k_val] = v_val
            elif isinstance(current, dict) and current.get('_array'):
                for _ in range(current['len']):
                    v_val, v_ph = _decode_value()
                    if v_ph:
                        queue.append(v_val)
                    current['items'].append(v_val)
                    
        # Recursively resolve placeholders
        def _resolve(obj):
            if isinstance(obj, dict):
                if obj.get('_map'):
                    return {k: _resolve(v) for k, v in obj['items'].items()}
                elif obj.get('_array'):
                    return [_resolve(v) for v in obj['items']]
            return obj
            
        return _resolve(root_obj)
