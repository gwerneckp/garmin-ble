"""Protocol constants pinned against the Garmin GFDI spec.

These numbers are the wire contract: a typo here is a silently misrouted
service, not a test failure somewhere useful.
"""

class TestGarminService:
    """GarminService enum values must match the Garmin GFDI spec."""

    def test_values(self):
        from garmin_ble.constants import GarminService
        assert GarminService.GFDI == 1
        assert GarminService.REGISTRATION == 4
        assert GarminService.REALTIME_HR == 6
        assert GarminService.REALTIME_STEPS == 7
        assert GarminService.REALTIME_CALORIES == 8
        assert GarminService.REALTIME_INTENSITY == 10
        assert GarminService.REALTIME_HRV == 12
        assert GarminService.REALTIME_STRESS == 13
        assert GarminService.REALTIME_ACCELEROMETER == 16
        assert GarminService.REALTIME_SPO2 == 19
        assert GarminService.REALTIME_BODY_BATTERY == 20
        assert GarminService.REALTIME_RESPIRATION == 21

    def test_is_int_enum(self):
        from garmin_ble.constants import GarminService
        assert int(GarminService.GFDI) == 1
        assert issubclass(GarminService, int)


class TestRequestType:
    """RequestType enum values must match the Garmin GFDI spec."""

    def test_values(self):
        from garmin_ble.constants import RequestType
        assert RequestType.REGISTER_ML_REQ == 0
        assert RequestType.REGISTER_ML_RESP == 1
        assert RequestType.CLOSE_HANDLE_REQ == 2
        assert RequestType.CLOSE_HANDLE_RESP == 3
        assert RequestType.CLOSE_ALL_REQ == 5
        assert RequestType.CLOSE_ALL_RESP == 6


class TestSystemEventType:
    """System-event types used by the heartbeat and reported to callers."""

    def test_values(self):
        from garmin_ble.constants import SystemEventType
        assert SystemEventType.SYNC_COMPLETE == 0
        assert SystemEventType.SYNC_FAIL == 1
        assert SystemEventType.TIME_UPDATED == 16


class TestGarminMessage:
    """GFDI message types this library sends, decodes, or dispatches on."""

    def test_values(self):
        from garmin_ble.constants import GarminMessage
        assert GarminMessage.RESPONSE == 5000
        assert GarminMessage.DEVICE_INFORMATION == 5024
        assert GarminMessage.SYSTEM_EVENT == 5030
        assert GarminMessage.PROTOBUF_REQUEST == 5043
        assert GarminMessage.PROTOBUF_RESPONSE == 5044
        assert GarminMessage.CURRENT_TIME_REQUEST == 5052


class TestConstants:
    def test_client_id(self):
        from garmin_ble.constants import CLIENT_ID
        assert CLIENT_ID == 2

    def test_base_uuid_format(self):
        from garmin_ble.constants import GARMIN_BASE_UUID
        uuid = GARMIN_BASE_UUID.format(0x2810)
        assert uuid == "6A4E2810-667B-11E3-949A-0800200C9A66"
