import pytest
from garmin_ble.parsers.protobuf_handler import ProtobufHandler
from garmin_ble.protobuf import gdi_smart_proto_pb2, gdi_device_status_pb2

def test_protobuf_handler_dispatch():
    handler = ProtobufHandler()
    
    # Create a mock response
    expected_response = gdi_smart_proto_pb2.Smart()
    expected_response.device_status_service.remote_device_battery_status_response.current_battery_level = 85
    
    def my_processor(sub_msg):
        # Verify it receives the correct sub-message
        assert sub_msg.HasField("remote_device_battery_status_request")
        return expected_response
        
    handler.register_processor("device_status_service", my_processor)
    
    # Send an incoming request
    incoming = gdi_smart_proto_pb2.Smart()
    incoming.device_status_service.remote_device_battery_status_request.SetInParent()
    
    response = handler.handle_incoming(incoming)
    assert response is expected_response
    
def test_protobuf_handler_no_match():
    handler = ProtobufHandler()
    
    # Send an incoming request with no registered handler
    incoming = gdi_smart_proto_pb2.Smart()
    incoming.device_status_service.remote_device_battery_status_request.SetInParent()
    
    response = handler.handle_incoming(incoming)
    assert response is None

def test_protobuf_handler_exception_handling(caplog):
    handler = ProtobufHandler()
    
    def bad_processor(sub_msg):
        raise ValueError("Something went wrong")
        
    handler.register_processor("device_status_service", bad_processor)
    
    incoming = gdi_smart_proto_pb2.Smart()
    incoming.device_status_service.remote_device_battery_status_request.SetInParent()
    
    # Should catch exception and return None instead of crashing
    response = handler.handle_incoming(incoming)
    assert response is None
    assert "Error in protobuf processor for 'device_status_service': Something went wrong" in caplog.text

def test_next_request_id():
    handler = ProtobufHandler()
    
    id1 = handler.next_request_id()
    id2 = handler.next_request_id()
    
    assert id1 == 1
    assert id2 == 2
    
    handler._request_id_counter = 65535
    id3 = handler.next_request_id()
    assert id3 == 0  # rollover
