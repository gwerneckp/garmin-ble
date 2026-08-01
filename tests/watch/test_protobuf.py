"""Protobuf over GFDI: correlated requests, responders, and device queries."""

import asyncio

import pytest

from garmin_ble import events
from garmin_ble.errors import RequestTimeout, UnroutableMessage
from garmin_ble.protobuf import gdi_device_status_pb2, gdi_find_my_watch_pb2
from garmin_ble.protobuf import gdi_installed_apps_service_pb2 as apps_pb2

DeviceStatus = gdi_device_status_pb2.DeviceStatusService
FindMyWatch = gdi_find_my_watch_pb2.FindMyWatchService
InstalledApps = apps_pb2.InstalledAppsService


class TestRequest:
    async def test_returns_the_matching_response(self, watch):
        response = await watch.request(DeviceStatus.RemoteDeviceBatteryStatusRequest())
        assert isinstance(response, DeviceStatus.RemoteDeviceBatteryStatusResponse)
        assert response.current_battery_level == 78

    async def test_caller_never_mentions_the_smart_envelope(self, watch):
        """The nesting is derived from the descriptors, not built by hand."""
        response = await watch.request(FindMyWatch.FindMyWatchRequest(timeout=5))
        assert isinstance(response, FindMyWatch.FindMyWatchResponse)

    async def test_concurrent_requests_get_their_own_answers(self, watch):
        """Correlation is by request id, so replies cannot be crossed."""
        battery, find, apps = await asyncio.gather(
            watch.request(DeviceStatus.RemoteDeviceBatteryStatusRequest()),
            watch.request(FindMyWatch.FindMyWatchRequest(timeout=1)),
            watch.request(InstalledApps.GetInstalledAppsRequest(appType=InstalledApps.AppType.ALL)),
        )
        assert isinstance(battery, DeviceStatus.RemoteDeviceBatteryStatusResponse)
        assert isinstance(find, FindMyWatch.FindMyWatchResponse)
        assert isinstance(apps, InstalledApps.GetInstalledAppsResponse)

    async def test_an_unanswered_request_times_out(self, watch):
        """Nothing answers a sync request, so it must not hang forever."""
        from garmin_ble.protobuf import gdi_core_pb2

        with pytest.raises(RequestTimeout) as exc:
            await watch.request(gdi_core_pb2.CoreService.SyncRequest(), timeout=0.3)
        assert exc.value.request_id is not None

    async def test_the_timeout_names_the_message(self, watch):
        from garmin_ble.protobuf import gdi_core_pb2

        with pytest.raises(RequestTimeout, match="SyncRequest"):
            await watch.request(gdi_core_pb2.CoreService.SyncRequest(), timeout=0.3)

    async def test_a_timed_out_request_is_forgotten(self, watch):
        from garmin_ble.protobuf import gdi_core_pb2

        with pytest.raises(RequestTimeout):
            await watch.request(gdi_core_pb2.CoreService.SyncRequest(), timeout=0.3)
        assert not watch._pending

    async def test_unroutable_messages_are_refused_before_sending(self, watch):
        with pytest.raises(UnroutableMessage):
            await watch.request(
                InstalledApps.InstalledApp(
                    storeAppId=b"\x01", type=InstalledApps.AppType.WATCH_APP,
                    name="x", disabled=False,
                )
            )

    async def test_latency_is_recorded(self, watch):
        await watch.request(DeviceStatus.RemoteDeviceBatteryStatusRequest())
        assert watch.diagnostics.requests_sent == 1
        assert watch.diagnostics.requests_answered == 1
        assert watch.diagnostics.latencies_ms


class TestRespondsTo:
    async def test_a_responder_answers_the_watch(self, watch):
        """The library wraps, frames, and correlates the reply."""
        asked = []

        @watch.responds_to(DeviceStatus.RemoteDeviceBatteryStatusRequest)
        def _(request):
            asked.append(request)
            return DeviceStatus.RemoteDeviceBatteryStatusResponse(current_battery_level=99)

        # Drive the watch's side by feeding it the request it would send.
        await _deliver_request(watch, DeviceStatus.RemoteDeviceBatteryStatusRequest())
        await asyncio.sleep(0.1)
        assert asked

    async def test_async_responders_work_too(self, watch):
        asked = []

        @watch.responds_to(DeviceStatus.RemoteDeviceBatteryStatusRequest)
        async def _(request):
            await asyncio.sleep(0)
            asked.append(request)
            return DeviceStatus.RemoteDeviceBatteryStatusResponse(current_battery_level=99)

        await _deliver_request(watch, DeviceStatus.RemoteDeviceBatteryStatusRequest())
        await asyncio.sleep(0.1)
        assert asked

    async def test_a_responder_returning_none_sends_nothing(self, watch):
        @watch.responds_to(DeviceStatus.RemoteDeviceBatteryStatusRequest)
        def _(request):
            return None

        await _deliver_request(watch, DeviceStatus.RemoteDeviceBatteryStatusRequest())
        await asyncio.sleep(0.1)  # must not raise

    async def test_a_raising_responder_does_not_break_the_link(self, watch):
        @watch.responds_to(DeviceStatus.RemoteDeviceBatteryStatusRequest)
        def _(request):
            raise RuntimeError("responder blew up")

        await _deliver_request(watch, DeviceStatus.RemoteDeviceBatteryStatusRequest())
        await asyncio.sleep(0.1)
        assert watch.is_connected

    async def test_incoming_protobufs_are_reported_as_events(self, watch):
        seen = []

        async def pump():
            async for event in watch.events():
                if isinstance(event, events.ProtobufReceived):
                    seen.append(event)

        task = asyncio.create_task(pump())
        await asyncio.sleep(0)
        try:
            await watch.request(DeviceStatus.RemoteDeviceBatteryStatusRequest())
            await asyncio.sleep(0.1)
            assert seen and seen[0].handled
        finally:
            task.cancel()


class TestDeviceQueries:
    async def test_battery(self, watch):
        battery = await watch.battery()
        assert battery.percent == 78
        assert battery.status == "ok"

    async def test_find_my_watch(self, watch):
        await watch.find_my_watch(duration=2)

    async def test_stop_find_my_watch(self, watch):
        await watch.stop_find_my_watch()

    async def test_installed_apps(self, watch):
        apps = await watch.installed_apps()
        assert [app.name for app in apps] == list(watch._transport.profile.apps)
        assert all(app.kind == "WATCH_APP" for app in apps)

    async def test_an_unanswered_query_raises_instead_of_reporting_nothing(self, watch):
        """Firmware that ignores a service must not look like "no apps installed"."""
        watch._transport._respond_to = lambda request: None
        with pytest.raises(RequestTimeout):
            await watch.installed_apps(timeout=0.3)


async def _deliver_request(watch, message):
    """Feed *message* to the watch as if the device had sent it."""
    from garmin_ble.constants import GarminService
    from garmin_ble.protocol import cobs, gfdi, mlr
    from garmin_ble.protocol.smart import SmartRouter

    body = SmartRouter.wrap(message).SerializeToString()
    frame = gfdi.GfdiMessageBuilder.build_protobuf_request(
        request_id=4242, data_offset=0, total_length=len(body), proto_bytes=body
    )
    handle = watch._handles[int(GarminService.GFDI)]
    await watch._on_packet(mlr.encode_rx(handle, cobs.CobsCoDec.encode(frame)))
