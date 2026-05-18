import asyncio
import struct
import sys
import os
from bleak import BleakScanner, BleakClient

from .constants import GARMIN_BASE_UUID, CLIENT_ID, GarminService, RequestType
from .cobs import CobsCoDec
from .gfdi import GfdiMessageBuilder

from .protobuf import gdi_smart_proto_pb2


class GarminClient:
    def __init__(self):
        self.client = None
        self.device = None
        self.rx_char = None
        self.tx_char = None
        self.service_handles = {}
        self.cobs = CobsCoDec()
        self.max_write_size = 20 # Conservative default
        
        # Event callbacks
        self.callbacks = {
            "hr": None,
            "steps": None,
            "hrv": None,
            "spo2": None,
            "respiration": None,
            "protobuf": None
        }

    def on(self, event_name, callback):
        """Register a callback for a specific telemetry event."""
        if event_name in self.callbacks:
            self.callbacks[event_name] = callback
        else:
            print(f"Warning: Unknown event type '{event_name}'")

    async def connect(self, timeout=5.0):
        print("Scanning for Garmin watches...")
        devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
        
        garmin_device = None
        all_found = []
        
        for address, (device, adv_data) in devices.items():
            name = device.name or adv_data.local_name or "Unknown"
            all_found.append(f" - {name} [{address}]")
            
            name_lower = name.lower()
            if 'garmin' in name_lower or 'fenix' in name_lower:
                garmin_device = device
                break

        if not garmin_device:
            print("Could not find a Garmin/Fenix device nearby. Devices found:")
            for d in all_found:
                print(d)
            return False

        print(f"Connecting to {garmin_device.name} [{garmin_device.address}]...")
        
        try:
            self.client = BleakClient(garmin_device.address)
            await self.client.connect()
            self.device = garmin_device
            
            try:
                # Attempt to get MTU size (if supported by OS/Bleak)
                mtu = self.client.mtu_size
                if mtu:
                    self.max_write_size = mtu - 3
                    print(f"Negotiated MTU size: {mtu} (Max write chunk: {self.max_write_size})")
            except AttributeError:
                print(f"MTU checking not supported, using default write chunk: {self.max_write_size}")
            
            print("Connected! Searching for endpoints...")
            
            for i in range(0x2810, 0x2815):
                rx_candidate = GARMIN_BASE_UUID.format(i).lower()
                tx_candidate = GARMIN_BASE_UUID.format(i + 0x10).lower()
                
                for service in self.client.services:
                    for char in service.characteristics:
                        if char.uuid == rx_candidate:
                            self.rx_char = rx_candidate
                            self.tx_char = tx_candidate
                            break
                    if self.rx_char: break
                if self.rx_char: break
                
            if not self.rx_char:
                print("Could not find Garmin proprietary characteristics.")
                return False
                
            print(f"Subscribing to RX channel: {self.rx_char}")
            await self.client.start_notify(self.rx_char, self._notify_handler)
            
            print("Initiating handshake...")
            payload = struct.pack('<bbhqb', 0, RequestType.CLOSE_ALL_REQ, 0, CLIENT_ID, 0)[:13]
            await self.client.write_gatt_char(self.tx_char, payload, response=False)
            return True
            
        except Exception as e:
            print(f"Connection error: {e}")
            return False

    async def start_sync_loop(self):
        """Keep the client alive and process events."""
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            if self.client and self.client.is_connected:
                await self.client.disconnect()

    def _build_register_req(self, service_code):
        return struct.pack('<bbqhb', 0, RequestType.REGISTER_ML_REQ, CLIENT_ID, service_code, 0)
        
    async def _send_gfdi_message(self, message: bytes):
        """Encode message with COBS and send over the GFDI handle with fragmentation."""
        gfdi_handle = None
        for h, s in self.service_handles.items():
            if s == GarminService.GFDI:
                gfdi_handle = h
                break
                
        if gfdi_handle is None:
            return
            
        encoded = CobsCoDec.encode(message)
        pos = 0
        while pos < len(encoded):
            chunk = encoded[pos : pos + self.max_write_size - 1]
            payload = bytes([gfdi_handle]) + chunk
            asyncio.create_task(self.client.write_gatt_char(self.tx_char, payload, response=False))
            pos += len(chunk)

    async def _notify_handler(self, sender, data):
        if not data: return

        is_mlr = (data[0] & 0x80) != 0
        handle = (data[0] & 0x70) >> 4 if is_mlr else data[0]

        if handle == 0x00 and not is_mlr:
            msg_type = data[1]
            if msg_type == RequestType.CLOSE_ALL_RESP:
                # Handshake step 2: Request telemetry streams
                for svc in [GarminService.GFDI, GarminService.REALTIME_HR, GarminService.REALTIME_STEPS, 
                            GarminService.REALTIME_HRV, GarminService.REALTIME_SPO2, GarminService.REALTIME_RESPIRATION]:
                    payload = self._build_register_req(svc)
                    asyncio.create_task(self.client.write_gatt_char(self.tx_char, payload, response=False))
                    
            elif msg_type == RequestType.REGISTER_ML_RESP:
                service_code = struct.unpack('<h', data[10:12])[0]
                status = data[12]
                assigned_handle = data[13]
                if status == 0:
                    self.service_handles[assigned_handle] = service_code
                    
        elif handle in self.service_handles:
            svc_id = self.service_handles[handle]
            
            if svc_id == GarminService.REALTIME_HR and len(data) >= 4:
                hr, rest = data[2], data[3]
                if self.callbacks["hr"]: self.callbacks["hr"](hr, rest)
                
            elif svc_id == GarminService.REALTIME_STEPS and len(data) >= 9:
                steps = struct.unpack('<I', data[1:5])[0]
                goal = struct.unpack('<I', data[5:9])[0]
                if self.callbacks["steps"]: self.callbacks["steps"](steps, goal)
                
            elif svc_id == GarminService.REALTIME_HRV and len(data) >= 3:
                rr = struct.unpack('<H', data[1:3])[0]
                if self.callbacks["hrv"]: self.callbacks["hrv"](rr)
                
            elif svc_id == GarminService.REALTIME_SPO2 and len(data) >= 2:
                spo2 = data[1]
                if spo2 != 255 and self.callbacks["spo2"]: self.callbacks["spo2"](spo2)
                    
            elif svc_id == GarminService.REALTIME_RESPIRATION and len(data) >= 2:
                breaths = struct.unpack('<b', bytes([data[1]]))[0]
                if breaths > 0 and self.callbacks["respiration"]: self.callbacks["respiration"](breaths)
                    
            elif svc_id == GarminService.GFDI:
                self._handle_gfdi(data[1:])

    def _handle_gfdi(self, payload):
        self.cobs.received_bytes(payload)
        while True:
            msg = self.cobs.retrieve_message()
            if not msg or len(msg) < 4:
                break
                
            message_type = struct.unpack('<H', msg[2:4])[0]
            if message_type == 5043 and len(msg) >= 18:  # PROTOBUF_REQUEST
                request_id = struct.unpack('<H', msg[4:6])[0]
                data_offset = struct.unpack('<I', msg[6:10])[0]
                total_len = struct.unpack('<I', msg[10:14])[0]
                proto_len = struct.unpack('<I', msg[14:18])[0]
                
                # We received the request, we must ACK it so the watch doesn't disconnect
                ack_msg = GfdiMessageBuilder.build_protobuf_ack(request_id, data_offset)
                asyncio.create_task(self._send_gfdi_message(ack_msg))
                
                if data_offset == 0 and total_len == proto_len:
                    try:
                        smart = gdi_smart_proto_pb2.Smart()
                        smart.ParseFromString(msg[18:18+proto_len])
                        if self.callbacks["protobuf"]:
                            self.callbacks["protobuf"](request_id, smart)
                    except Exception as e:
                        print(f"Error parsing Protobuf: {e}")
