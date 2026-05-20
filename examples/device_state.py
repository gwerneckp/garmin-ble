import asyncio
import logging
from garmin_ble import GarminClient
from garmin_ble.logging import configure
from garmin_ble.protobuf import gdi_device_status_pb2, gdi_smart_proto_pb2

def handle_device_status(device_status):
    """
    Handle incoming device status messages.
    """
    # 1. Handle the watch's response to our battery level request
    if device_status.HasField("remote_device_battery_status_response"):
        response = device_status.remote_device_battery_status_response
        print(f"\n🔋 Watch battery level: {response.current_battery_level}%")
        return None
        
    # 2. Handle the watch asking US for our phone battery level
    if device_status.HasField("remote_device_battery_status_request"):
        print("\n📱 Watch is asking for the phone's battery level! Replying with 99%...")
        return gdi_smart_proto_pb2.Smart(
            device_status_service=gdi_device_status_pb2.DeviceStatusService(
                remote_device_battery_status_response=
                    gdi_device_status_pb2.DeviceStatusService.RemoteDeviceBatteryStatusResponse(
                        status=0,
                        current_battery_level=99,
                        current_charge_state=0
                    )
            )
        )
    
    return None

async def main():
    # configure(level=logging.DEBUG)
    
    client = GarminClient()
    
    # This will catch both our responses and the watch's own requests!
    client.protobuf_handler.register_processor("device_status_service", handle_device_status)
    
    if await client.connect():
        print("✅ Connected to watch! Waiting for handshake to complete...")
        
        # Give the watch a moment to complete the handshake and register the GFDI service
        await asyncio.sleep(2.0)
        
        # Build a request to ask the watch for its battery level
        request = gdi_smart_proto_pb2.Smart(
            device_status_service=gdi_device_status_pb2.DeviceStatusService(
                remote_device_battery_status_request=
                    gdi_device_status_pb2.DeviceStatusService.RemoteDeviceBatteryStatusRequest()
            )
        )
        
        print("📡 Sending battery status request to the watch...")
        await client.send_protobuf(request)
        
        try:
            # Run indefinitely to listen for the response
            await client.run_forever()
        except KeyboardInterrupt:
            print("\nDisconnecting...")
            await client.disconnect()
        finally:
            if client.is_connected:
                await client.disconnect()
    else:
        print("❌ Failed to connect.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
