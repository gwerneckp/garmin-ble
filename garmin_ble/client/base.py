import asyncio
import struct
from typing import Callable, Dict, Optional
from bleak import BleakClient

from ..constants import GARMIN_BASE_UUID, CLIENT_ID, GarminService, RequestType
from ..cobs import CobsCoDec
from ..gfdi import GfdiMessageBuilder
from ..protobuf import gdi_smart_proto_pb2
from ..logging import get_logger

log = get_logger(__name__)


class GarminClientBase:
    """Engine for Garmin watch communication.

    Handles low-level BLE connectivity, MLR handle multiplexing, 
    GFDI message routing, and telemetry parsing.
    """

    def __init__(self):
        self.client: Optional[BleakClient] = None
        self.device = None
        self.address: Optional[str] = None
        self.rx_char: Optional[str] = None
        self.tx_char: Optional[str] = None
        
        self.service_handles: Dict[int, int] = {}  # handle -> service_code
        self._service_handlers: Dict[int, Callable[[bytes], None]] = {} # service_code -> callback
        
        # High-level event callbacks
        self.callbacks: Dict[str, Callable] = {
            "hr": None,
            "steps": None,
            "hrv": None,
            "spo2": None,
            "respiration": None,
            "protobuf": None
        }
        
        self.cobs = CobsCoDec()
        self.max_write_size = 20  # Conservative default
        self._is_connected = False

    @property
    def is_connected(self) -> bool:
        return self._is_connected and self.client and self.client.is_connected

    def on(self, event_name: str, callback: Callable):
        """Register a callback for a specific high-level telemetry event."""
        if event_name in self.callbacks:
            self.callbacks[event_name] = callback
        else:
            log.warning("Unknown event type '%s'", event_name)

    def register_service_handler(self, service_code: int, callback: Callable[[bytes], None]):
        """Register a callback for raw data received on a specific service code."""
        self._service_handlers[service_code] = callback

    async def connect(self, address: str, timeout: float = 5.0) -> bool:
        """Connect to a Garmin watch at a known BLE address."""
        log.info("Connecting to %s ...", address)
        self.address = address

        try:
            self.client = BleakClient(address)
            await self.client.connect(timeout=timeout)
            self._is_connected = True
        except Exception as e:
            log.error("Connection failed: %s", e)
            self._is_connected = False
            return False

        try:
            mtu = self.client.mtu_size
            if mtu:
                self.max_write_size = mtu - 3
                log.debug("Negotiated MTU size: %d (Max write chunk: %d)", mtu, self.max_write_size)
        except AttributeError:
            log.debug("MTU checking not supported, using default write chunk: %d", self.max_write_size)

        log.info("Connected! Searching for endpoints...")

        # Search for Garmin proprietary characteristics
        self.rx_char = None
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
            log.error("Could not find Garmin proprietary characteristics.")
            await self.disconnect()
            return False

        log.info("Subscribing to RX channel: %s", self.rx_char)
        await self.client.start_notify(self.rx_char, self._notify_handler)

        log.info("Initiating handshake (CLOSE_ALL)...")
        # CLOSE_ALL_REQ resets all MLR assignments on the watch
        payload = struct.pack('<bbhqb', 0, RequestType.CLOSE_ALL_REQ, 0, CLIENT_ID, 0)[:13]
        await self.client.write_gatt_char(self.tx_char, payload, response=False)
        return True

    async def disconnect(self):
        """Disconnect from the watch and clean up."""
        self._is_connected = False
        if self.client and self.client.is_connected:
            await self.client.disconnect()
        self.service_handles.clear()

    async def send_gfdi_message(self, message: bytes):
        """Encode message with COBS and send over the GFDI handle with fragmentation."""
        gfdi_handle = self.get_handle_for_service(GarminService.GFDI)
        if gfdi_handle is None:
            log.warning("Cannot send GFDI message: GFDI service not registered.")
            return
            
        encoded = CobsCoDec.encode(message)
        pos = 0
        while pos < len(encoded):
            chunk = encoded[pos : pos + self.max_write_size - 1]
            payload = bytes([gfdi_handle]) + chunk
            await self.client.write_gatt_char(self.tx_char, payload, response=False)
            pos += len(chunk)

    def get_handle_for_service(self, service_code: int) -> Optional[int]:
        """Return the dynamic handle assigned to a service code, if any."""
        for handle, svc in self.service_handles.items():
            if svc == service_code:
                return handle
        return None

    async def request_service_registration(self, service_code: int):
        """Send a request to the watch to assign a handle for a specific service."""
        log.debug("Requesting registration for service code: %d", service_code)
        payload = struct.pack('<bbqhb', 0, RequestType.REGISTER_ML_REQ, CLIENT_ID, service_code, 0)
        if self.is_connected:
            await self.client.write_gatt_char(self.tx_char, payload, response=False)

    async def _notify_handler(self, sender, data: bytes):
        if not data: return

        # MLR Multiplexing Logic
        is_mlr = (data[0] & 0x80) != 0
        handle = (data[0] & 0x70) >> 4 if is_mlr else data[0]

        # Handle 0x00 is the Control Channel
        if handle == 0x00 and not is_mlr:
            msg_type = data[1]
            if msg_type == RequestType.CLOSE_ALL_RESP:
                self.service_handles.clear()
                log.debug("Control Channel: CLOSE_ALL confirmed.")
                self._on_handshake_reset()
                    
            elif msg_type == RequestType.REGISTER_ML_RESP:
                service_code = struct.unpack('<h', data[10:12])[0]
                status = data[12]
                assigned_handle = data[13]
                if status == 0:
                    self.service_handles[assigned_handle] = service_code
                    log.debug("Service registered: %d -> Handle 0x%02X", service_code, assigned_handle)
                    
        elif handle in self.service_handles:
            service_code = self.service_handles[handle]
            
            if service_code == GarminService.REALTIME_HR:
                self._parse_hr(data)
            elif service_code == GarminService.REALTIME_STEPS:
                self._parse_steps(data)
            elif service_code == GarminService.REALTIME_HRV:
                self._parse_hrv(data)
            elif service_code == GarminService.REALTIME_SPO2:
                self._parse_spo2(data)
            elif service_code == GarminService.REALTIME_RESPIRATION:
                self._parse_respiration(data)
            elif service_code == GarminService.GFDI:
                self._handle_gfdi_raw(data[1:])
            elif service_code in self._service_handlers:
                self._service_handlers[service_code](data)

    def _on_handshake_reset(self):
        """Override in subclass to register services after CLOSE_ALL."""
        pass

    def _parse_hr(self, data: bytes):
        if len(data) >= 4:
            hr, rest = data[2], data[3]
            if self.callbacks["hr"]: self.callbacks["hr"](hr, rest)

    def _parse_steps(self, data: bytes):
        if len(data) >= 9:
            steps = struct.unpack('<I', data[1:5])[0]
            goal = struct.unpack('<I', data[5:9])[0]
            if self.callbacks["steps"]: self.callbacks["steps"](steps, goal)

    def _parse_hrv(self, data: bytes):
        if len(data) >= 3:
            rr = struct.unpack('<H', data[1:3])[0]
            if self.callbacks["hrv"]: self.callbacks["hrv"](rr)

    def _parse_spo2(self, data: bytes):
        if len(data) >= 2:
            spo2 = data[1]
            if spo2 != 255 and self.callbacks["spo2"]:
                self.callbacks["spo2"](spo2)

    def _parse_respiration(self, data: bytes):
        if len(data) >= 2:
            breaths = struct.unpack('<b', bytes([data[1]]))[0]
            if breaths > 0 and self.callbacks["respiration"]:
                self.callbacks["respiration"](breaths)

    def _handle_gfdi_raw(self, payload: bytes):
        """Assembles COBS chunks and dispatches complete GFDI messages."""
        self.cobs.received_bytes(payload)
        while True:
            msg = self.cobs.retrieve_message()
            if not msg: break
            self._handle_gfdi_msg(msg)

    def _handle_gfdi_msg(self, msg: bytes):
        if len(msg) < 4: return
        
        message_type = struct.unpack('<H', msg[2:4])[0]
        if message_type == 5043 and len(msg) >= 18:  # PROTOBUF_REQUEST
            request_id = struct.unpack('<H', msg[4:6])[0]
            data_offset = struct.unpack('<I', msg[6:10])[0]
            total_len = struct.unpack('<I', msg[10:14])[0]
            proto_len = struct.unpack('<I', msg[14:18])[0]
            
            # Auto-ACK protobuf requests
            ack_msg = GfdiMessageBuilder.build_protobuf_ack(request_id, data_offset)
            asyncio.create_task(self.send_gfdi_message(ack_msg))
            
            if data_offset == 0 and total_len == proto_len:
                try:
                    smart = gdi_smart_proto_pb2.Smart()
                    smart.ParseFromString(msg[18:18+proto_len])
                    if self.callbacks["protobuf"]:
                        self.callbacks["protobuf"](request_id, smart)
                except Exception as e:
                    log.error("Error parsing Protobuf: %s", e)

    async def run_forever(self):
        """Simple keep-alive loop."""
        try:
            while self.is_connected:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await self.disconnect()
