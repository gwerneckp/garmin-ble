import threading
from typing import Callable, Dict, Optional, Any
from ..logging import get_logger
from ..protobuf import gdi_smart_proto_pb2

log = get_logger(__name__)

class ProtobufHandler:
    """Dispatch layer for incoming/outgoing protobuf Smart messages.
    
    Register processors for specific service fields. When a Smart message
    arrives, the handler checks which service field is set and calls the
    matching processor. If the processor returns a Smart response, it is
    sent back to the watch automatically.
    
    The library ships with NO default processors — callers register their own.
    """
    
    def __init__(self):
        self._processors: Dict[str, Callable[[Any], Optional[gdi_smart_proto_pb2.Smart]]] = {}
        self._request_id_counter = 0
        self._request_id_lock = threading.Lock()
    
    def register_processor(self, field_name: str, callback: Callable[[Any], Optional[gdi_smart_proto_pb2.Smart]]):
        """Register a handler for a protobuf service field.
        
        Args:
            field_name: one of the Smart message fields, e.g. 
                "device_status_service", "core_service", "find_my_watch_service"
            callback: A callable that receives the service sub-message and returns
                a Smart response (or None).
        """
        self._processors[field_name] = callback

    def handle_incoming(self, smart: gdi_smart_proto_pb2.Smart) -> Optional[gdi_smart_proto_pb2.Smart]:
        """Dispatch an incoming Smart to the registered processor(s).
        Returns the response Smart, or None if no processor matched or no response needed.
        """
        # A Smart message typically has one of its service fields set
        for field_name, processor in self._processors.items():
            if smart.HasField(field_name):
                try:
                    sub_message = getattr(smart, field_name)
                    response = processor(sub_message)
                    if response is not None:
                        return response
                except Exception as e:
                    log.error("Error in protobuf processor for '%s': %s", field_name, e)
        return None
    
    def next_request_id(self) -> int:
        """Auto-incrementing request ID for outgoing protobuf messages."""
        with self._request_id_lock:
            self._request_id_counter = (self._request_id_counter + 1) % 65536
            return self._request_id_counter
