"""Descriptor-driven routing of protobuf messages in and out of ``Smart``."""

import pytest

from garmin_ble.errors import UnroutableMessage
from garmin_ble.protobuf import (
    gdi_core_pb2,
    gdi_device_status_pb2,
    gdi_find_my_watch_pb2,
    gdi_installed_apps_service_pb2,
    gdi_smart_proto_pb2,
)
from garmin_ble.protocol.smart import SmartRouter

DeviceStatus = gdi_device_status_pb2.DeviceStatusService
FindMyWatch = gdi_find_my_watch_pb2.FindMyWatchService
InstalledApps = gdi_installed_apps_service_pb2.InstalledAppsService
Core = gdi_core_pb2.CoreService


class TestRoutingTable:
    def test_table_is_derived_from_the_descriptors(self):
        """Every service field of Smart contributes its message types."""
        assert len(SmartRouter._table()) > 40

    def test_known_messages_are_routable(self):
        for message in (
            DeviceStatus.RemoteDeviceBatteryStatusRequest(),
            FindMyWatch.FindMyWatchRequest(timeout=5),
            InstalledApps.GetInstalledAppsRequest(appType=InstalledApps.AppType.ALL),
            Core.SyncRequest(),
        ):
            assert SmartRouter.routable(message)

    def test_the_envelope_itself_is_not_a_routable_payload(self):
        assert not SmartRouter.routable(gdi_smart_proto_pb2.Smart())


class TestWrap:
    def test_wraps_a_request_into_the_right_service_field(self):
        smart = SmartRouter.wrap(DeviceStatus.RemoteDeviceBatteryStatusRequest())
        assert smart.HasField("device_status_service")
        assert smart.device_status_service.HasField("remote_device_battery_status_request")

    def test_empty_request_still_marks_its_field_present(self):
        """An all-defaults message would otherwise serialise to nothing."""
        smart = SmartRouter.wrap(DeviceStatus.RemoteDeviceBatteryStatusRequest())
        assert smart.SerializeToString() != b""

    def test_field_values_survive(self):
        smart = SmartRouter.wrap(
            DeviceStatus.RemoteDeviceBatteryStatusResponse(current_battery_level=85)
        )
        assert (
            smart.device_status_service.remote_device_battery_status_response.current_battery_level
            == 85
        )

    def test_wrapping_an_envelope_is_a_no_op(self):
        smart = gdi_smart_proto_pb2.Smart()
        assert SmartRouter.wrap(smart) is smart

    def test_unplaceable_message_is_refused(self):
        """A message that is not a field of any service cannot be sent alone."""
        with pytest.raises(UnroutableMessage):
            SmartRouter.wrap(InstalledApps.InstalledApp(name="x", type=1, disabled=False,
                                                        storeAppId=b"\x01"))

    def test_refusal_names_the_message(self):
        with pytest.raises(UnroutableMessage, match="InstalledApp"):
            SmartRouter.wrap(InstalledApps.InstalledApp(name="x", type=1, disabled=False,
                                                        storeAppId=b"\x01"))


class TestUnwrap:
    @pytest.mark.parametrize(
        "message",
        [
            DeviceStatus.RemoteDeviceBatteryStatusRequest(),
            DeviceStatus.RemoteDeviceBatteryStatusResponse(current_battery_level=42),
            FindMyWatch.FindMyWatchRequest(timeout=5),
            FindMyWatch.FindMyWatchCancelRequest(),
            InstalledApps.GetInstalledAppsRequest(appType=InstalledApps.AppType.ALL),
            Core.SyncRequest(),
        ],
        ids=lambda msg: type(msg).__name__,
    )
    def test_wrap_then_unwrap_is_identity(self, message):
        unwrapped = SmartRouter.unwrap(SmartRouter.wrap(message))
        assert type(unwrapped) is type(message)
        assert unwrapped == message

    def test_survives_serialisation(self):
        """The full path a message takes over the wire."""
        original = DeviceStatus.RemoteDeviceBatteryStatusResponse(current_battery_level=77)
        raw = SmartRouter.wrap(original).SerializeToString()

        received = gdi_smart_proto_pb2.Smart()
        received.ParseFromString(raw)
        assert SmartRouter.unwrap(received) == original

    def test_empty_envelope_unwraps_to_nothing(self):
        assert SmartRouter.unwrap(gdi_smart_proto_pb2.Smart()) is None

    def test_repeated_fields_are_not_mistaken_for_the_payload(self):
        """`installedApps` is repeated; the response itself is the payload."""
        response = InstalledApps.GetInstalledAppsResponse(
            availableSpace=1024,
            availableSlots=3,
            installedApps=[
                InstalledApps.InstalledApp(
                    storeAppId=b"\x01\x02", type=InstalledApps.AppType.WATCH_APP,
                    name="Trail Run", disabled=False,
                )
            ],
        )
        unwrapped = SmartRouter.unwrap(SmartRouter.wrap(response))
        assert type(unwrapped) is InstalledApps.GetInstalledAppsResponse
        assert unwrapped.installedApps[0].name == "Trail Run"

    def test_service_name_is_reported(self):
        smart = SmartRouter.wrap(DeviceStatus.RemoteDeviceBatteryStatusRequest())
        assert SmartRouter.service_name(smart) == "device_status_service"

    def test_service_name_of_an_empty_envelope(self):
        assert SmartRouter.service_name(gdi_smart_proto_pb2.Smart()) is None
