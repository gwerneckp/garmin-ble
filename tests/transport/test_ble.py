"""The bleak-backed transport: discovery, characteristics, MTU.

These are the only tests that mock bleak, because they are the only ones about
bleak. Everything above the transport runs against the simulator instead.
"""

import pytest

from garmin_ble.errors import CHARACTERISTIC_DISCOVERY, ConnectionFailed, HandshakeError, WatchNotFound
from garmin_ble.transport.ble import BleTransport, looks_like_garmin


class TestNameMatching:
    @pytest.mark.parametrize(
        "name",
        ["Garmin Watch", "fenix 7", "Forerunner 265", "Venu 3", "Instinct 2",
         "epix Pro", "vivoactive 5", "GARMIN FENIX"],
    )
    def test_garmin_names_match(self, name):
        assert looks_like_garmin(name)

    @pytest.mark.parametrize("name", ["AirPods", "Tile", "", None, "MyPhone"])
    def test_other_names_do_not(self, name):
        assert not looks_like_garmin(name)


class TestDiscovery:
    async def test_finds_a_garmin_device(self, mock_scan, mock_ble):
        mock_scan(("fenix 7", "AA:BB:CC:DD:EE:FF"), ("AirPods", "11:22:33:44:55:66"))
        link = await BleTransport(scan_timeout=0.1).open()
        assert link.address == "AA:BB:CC:DD:EE:FF"
        assert link.name == "fenix 7"

    async def test_no_match_raises_with_the_devices_it_saw(self, mock_scan):
        """The candidate list is what makes this error actionable."""
        mock_scan(("AirPods", "11:22:33:44:55:66"), ("Tile", "77:88:99:AA:BB:CC"))
        with pytest.raises(WatchNotFound) as exc:
            await BleTransport(scan_timeout=0.1).open()
        assert {d.name for d in exc.value.candidates} == {"AirPods", "Tile"}
        assert "AirPods" in str(exc.value)

    async def test_an_empty_scan_says_so(self, mock_scan):
        mock_scan()
        with pytest.raises(WatchNotFound, match="no BLE devices seen at all"):
            await BleTransport(scan_timeout=0.1).open()

    async def test_a_name_pattern_narrows_the_search(self, mock_scan, mock_ble):
        mock_scan(("fenix 7", "AA:BB:CC:DD:EE:FF"), ("Venu 3", "11:22:33:44:55:66"))
        link = await BleTransport(name_pattern="venu*", scan_timeout=0.1).open()
        assert link.address == "11:22:33:44:55:66"

    async def test_an_unmatched_pattern_names_the_pattern(self, mock_scan):
        mock_scan(("fenix 7", "AA:BB:CC:DD:EE:FF"))
        with pytest.raises(WatchNotFound, match="venu"):
            await BleTransport(name_pattern="venu*", scan_timeout=0.1).open()

    async def test_a_known_address_skips_the_scan(self, mock_ble, mocker):
        scan = mocker.patch("garmin_ble.transport.ble.BleakScanner.discover")
        link = await BleTransport(address="AA:BB:CC:DD:EE:FF").open()
        assert link.address == "AA:BB:CC:DD:EE:FF"
        scan.assert_not_called()


class TestConnection:
    async def test_subscribes_to_the_rx_characteristic(self, mock_ble):
        await BleTransport(address="AA:BB:CC:DD:EE:FF").open()
        mock_ble.start_notify.assert_awaited_once()
        assert "2810" in mock_ble.start_notify.call_args[0][0]

    async def test_mtu_sets_the_write_budget(self, mock_ble, mocker):
        mocker.patch.object(type(mock_ble), "mtu_size", 100)
        link = await BleTransport(address="AA:BB:CC:DD:EE:FF").open()
        assert link.mtu == 100
        assert link.max_write == 97

    async def test_an_unreported_mtu_falls_back_to_the_ble_default(self, mock_ble):
        link = await BleTransport(address="AA:BB:CC:DD:EE:FF").open()
        assert link.mtu == 23

    async def test_a_device_without_garmin_characteristics_is_rejected(self, mock_ble):
        """Connecting to the wrong device should say so, not time out later."""
        mock_ble.services = []
        with pytest.raises(HandshakeError) as exc:
            await BleTransport(address="AA:BB:CC:DD:EE:FF").open()
        assert exc.value.stage == CHARACTERISTIC_DISCOVERY

    async def test_a_refused_connection_raises(self, mock_ble):
        mock_ble.connect.side_effect = OSError("device not reachable")
        with pytest.raises(ConnectionFailed, match="device not reachable"):
            await BleTransport(address="AA:BB:CC:DD:EE:FF").open()

    async def test_writes_go_to_the_tx_characteristic(self, mock_ble):
        transport = BleTransport(address="AA:BB:CC:DD:EE:FF")
        await transport.open()
        await transport.write(b"\x00\x05")

        uuid, payload = mock_ble.write_gatt_char.call_args[0][:2]
        assert "2820" in uuid  # TX is RX + 0x10
        assert payload == b"\x00\x05"

    async def test_writing_on_a_closed_link_raises(self):
        with pytest.raises(ConnectionFailed):
            await BleTransport(address="AA:BB:CC:DD:EE:FF").write(b"\x00")

    async def test_close_is_safe_when_already_closed(self, mock_ble):
        transport = BleTransport(address="AA:BB:CC:DD:EE:FF")
        await transport.open()
        await transport.close()
        await transport.close()

    async def test_inbound_notifications_are_delivered(self, mock_ble):
        transport = BleTransport(address="AA:BB:CC:DD:EE:FF")
        received = []

        async def sink(data):
            received.append(data)

        transport.on_notify = sink
        await transport.open()

        handler = mock_ble.start_notify.call_args[0][1]
        await handler(None, bytearray(b"\x80\x01\x02"))
        assert received == [b"\x80\x01\x02"]

    async def test_an_unexpected_drop_is_reported(self, mock_ble):
        transport = BleTransport(address="AA:BB:CC:DD:EE:FF")
        reasons = []
        transport.on_disconnect = reasons.append
        await transport.open()

        # bleak invokes the callback it was handed at construction.
        transport._on_bleak_disconnect(mock_ble)
        assert reasons == ["BLE link lost"]

    async def test_a_deliberate_close_is_not_reported_as_a_drop(self, mock_ble):
        transport = BleTransport(address="AA:BB:CC:DD:EE:FF")
        reasons = []
        transport.on_disconnect = reasons.append
        await transport.open()
        await transport.close()

        transport._on_bleak_disconnect(mock_ble)
        assert reasons == []
