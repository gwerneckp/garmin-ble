import asyncio
import struct
from typing import Callable, Dict, Optional
from bleak import BleakClient

from ..constants import GARMIN_BASE_UUID, CLIENT_ID, GarminService, RequestType, GarminMessage
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
            "calories": None,
            "intensity": None,
            "stress": None,
            "accel": None,
            "body_battery": None,
            "protobuf": None,
            "disconnected": None,
            "system_event": None,
        }
        
        self.cobs = CobsCoDec()
        self.max_write_size = 20  # Conservative default
        self._is_connected = False

        # Heartbeat (periodic time-sync) task
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._heartbeat_interval: float = 60.0

    @property
    def is_connected(self) -> bool:
        return self._is_connected and self.client and self.client.is_connected

    def _disconnected_callback(self, client: BleakClient):
        log.info("BLE Device disconnected.")
        self._is_connected = False
        if self.callbacks["disconnected"]:
            self.callbacks["disconnected"]()

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
        if self.is_connected:
            log.debug("Already connected to %s", self.address)
            return True

        log.info("Connecting to %s ...", address)
        self.address = address

        try:
            self.client = BleakClient(address, disconnected_callback=self._disconnected_callback)
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
        await self.disable_heartbeat()
        if self.client and self.client.is_connected:
            await self.client.disconnect()
        self.service_handles.clear()

    def enable_heartbeat(self, interval: float = 60.0):
        """Start sending periodic TIME_UPDATED system events to the watch.

        This keeps the connection alive and lets the watch know our current
        time. The legacy Gadgetbridge code calls ``onSetTime()`` which sends
        ``SystemEventMessage(TIME_UPDATED, 0)``.
        """
        self._heartbeat_interval = interval
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            log.debug("Heartbeat enabled (interval=%.1fs).", interval)

    async def disable_heartbeat(self):
        """Stop the periodic heartbeat."""
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        self._heartbeat_task = None

    async def _heartbeat_loop(self):
        """Periodically send TIME_UPDATED to keep the connection alive."""
        try:
            while self._is_connected:
                await asyncio.sleep(self._heartbeat_interval)
                if not self._is_connected:
                    break
                msg = GfdiMessageBuilder.build_system_event(
                    GarminClientBase.SYSTEM_EVENT_TIME_UPDATED, 0)
                await self.send_gfdi_message(msg)
                log.debug("Heartbeat: sent TIME_UPDATED.")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error("Heartbeat error: %s", e)

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

    async def send_service_command(self, service_code: int, command: bytes):
        """Send a raw command byte/packet to a registered service."""
        handle = self.get_handle_for_service(service_code)
        if handle is None:
            log.warning("Cannot send command: service %d not registered.", service_code)
            return

        payload = bytes([handle]) + command
        if self.is_connected:
            await self.client.write_gatt_char(self.tx_char, payload, response=False)

    async def register_and_start_service(self, service_code: int):
        """Register a service and send the 0x01 start command.

        This is the manual alternative for services that the library no
        longer starts automatically (Accelerometer, Calories, Intensity).

        Example usage::

            await client.register_and_start_service(GarminService.REALTIME_ACCELEROMETER)
            await client.register_and_start_service(GarminService.REALTIME_CALORIES)
            await client.register_and_start_service(GarminService.REALTIME_INTENSITY)
        """
        await self.request_service_registration(service_code)
        # Give the watch a moment to assign the handle
        await asyncio.sleep(0.5)
        await self.send_service_command(service_code, b"\x01")
        log.info("Service %d registered and started manually.", service_code)

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
                    self._on_service_registered(service_code, assigned_handle)
                    
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
            elif service_code == GarminService.REALTIME_CALORIES:
                self._parse_calories(data)
            elif service_code == GarminService.REALTIME_INTENSITY:
                self._parse_intensity(data)
            elif service_code == GarminService.REALTIME_STRESS:
                self._parse_stress(data)
            elif service_code == GarminService.REALTIME_ACCELEROMETER:
                self._parse_accel(data)
            elif service_code == GarminService.REALTIME_BODY_BATTERY:
                self._parse_body_battery(data)
            elif service_code == GarminService.GFDI:
                self._handle_gfdi_raw(data[1:])
            elif service_code in self._service_handlers:
                self._service_handlers[service_code](data)

    def _on_handshake_reset(self):
        """Override in subclass to register services after CLOSE_ALL."""
        pass

    def _on_service_registered(self, service_code: int, handle: int):
        """Override in subclass to react when a service is registered."""
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

    def _parse_calories(self, data: bytes):
        """Parse Calories (Service 8). Format: [total_cal (uint32 LE), active_cal (uint32 LE)]."""
        if len(data) >= 9:
            total = struct.unpack('<I', data[1:5])[0]
            active = struct.unpack('<I', data[5:9])[0]
            if self.callbacks["calories"]: self.callbacks["calories"](total, active)

    def _parse_intensity(self, data: bytes):
        """Parse Intensity Minutes (Service 10). Format: [moderate (uint16 LE), vigorous (uint16 LE)]."""
        if len(data) >= 5:
            mod = struct.unpack('<H', data[1:3])[0]
            vig = struct.unpack('<H', data[3:5])[0]
            if self.callbacks["intensity"]: self.callbacks["intensity"](mod, vig)

    def _parse_stress(self, data: bytes):
        """Parse Stress Level (Service 13). Format: [level (int8)]."""
        if len(data) >= 2:
            level = struct.unpack('<b', bytes([data[1]]))[0]
            if self.callbacks["stress"]: self.callbacks["stress"](level)

    def _parse_accel(self, data: bytes):
        """Parse Accelerometer (Service 16). Returns list of (X, Y, Z) float samples (in g)."""
        if not self.callbacks["accel"] or len(data) < 17:
            return

        # data[0] is the service header (0x10)
        # data[1:3] is the 16-bit timestamp (ignored)
        payload = data[3:17]

        vals = []
        for i in range(4):
            b0, b1, b2 = payload[3*i], payload[3*i+1], payload[3*i+2]
            v_even = b0 | ((b1 & 0x0F) << 8)
            v_odd  = (b1 >> 4) | (b2 << 4)
            vals.extend([v_even, v_odd])
        
        b0, b1 = payload[12], payload[13]
        v8 = b0 | ((b1 & 0x0F) << 8)
        vals.append(v8)

        # 12-bit signed -> float (1g = 256 LSB)
        g_vals = [(v if v < 2048 else v - 4096) / 256.0 for v in vals]

        samples = [
            (g_vals[0], g_vals[1], g_vals[2]),
            (g_vals[3], g_vals[4], g_vals[5]),
            (g_vals[6], g_vals[7], g_vals[8]),
        ]
        
        self.callbacks["accel"](samples)

    def _parse_body_battery(self, data: bytes):
        """Parse Body Battery (Service 20). Format: [level (int8)]."""
        if len(data) >= 2:
            level = struct.unpack('<b', bytes([data[1]]))[0]
            if self.callbacks["body_battery"]: self.callbacks["body_battery"](level)

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

        # Ack every incoming GFDI message (port of GenericStatusMessage(ACK))
        ack_msg = GfdiMessageBuilder.build_status_ack(message_type)
        asyncio.create_task(self.send_gfdi_message(ack_msg))

        if message_type == GarminMessage.PROTOBUF_REQUEST and len(msg) >= 18:
            request_id = struct.unpack('<H', msg[4:6])[0]
            data_offset = struct.unpack('<I', msg[6:10])[0]
            total_len = struct.unpack('<I', msg[10:14])[0]
            proto_len = struct.unpack('<I', msg[14:18])[0]

            # Override: use the richer protobuf-specific ACK instead of the simple one
            rich_ack = GfdiMessageBuilder.build_protobuf_ack(request_id, data_offset)
            asyncio.create_task(self.send_gfdi_message(rich_ack))

            if data_offset == 0 and total_len == proto_len:
                try:
                    smart = gdi_smart_proto_pb2.Smart()
                    smart.ParseFromString(msg[18:18+proto_len])
                    if self.callbacks["protobuf"]:
                        self.callbacks["protobuf"](request_id, smart)
                except Exception as e:
                    log.error("Error parsing Protobuf: %s", e)

        elif message_type == GarminMessage.CURRENT_TIME_REQUEST:
            log.debug("Received Time Sync Request. Responding...")
            time_msg = GfdiMessageBuilder.build_time_response()
            asyncio.create_task(self.send_gfdi_message(time_msg))

        elif message_type == GarminMessage.SYSTEM_EVENT and len(msg) >= 5:
            event_type = msg[4]
            event_value = msg[5] if len(msg) > 5 else 0
            log.debug("Received SYSTEM_EVENT: type=%d value=%d", event_type, event_value)
            if self.callbacks["system_event"]:
                self.callbacks["system_event"](event_type, event_value)

    async def run_forever(self):
        """Simple keep-alive loop."""
        try:
            while self.is_connected:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await self.disconnect()
