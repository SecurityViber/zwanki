"""Heart rate from a Bluetooth chest strap (Polar H10 or any standard Heart Rate Service sensor)."""

import time

from bleak import BleakClient, BleakScanner

from .log import log

HR_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
HR_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"
FRESH_SECONDS = 5  # a reading older than this counts as no reading


class HeartRateError(Exception):
    pass


def parse_heart_rate(data):
    """Beats per minute from a Heart Rate Measurement, or None without a reading (e.g. no skin contact)."""
    if len(data) < 2:
        return None
    flags = data[0]
    if flags & 0b110 == 0b100:  # contact detection supported, but no contact
        return None
    if flags & 0x01:
        bpm = int.from_bytes(data[1:3], "little") if len(data) >= 3 else 0
    else:
        bpm = data[1]
    return bpm or None  # straps send 0 while they have no reading (e.g. not worn)


class HeartRateMonitor:
    def __init__(self, on_change=None):
        self.on_change = on_change  # called with bpm (or None) whenever it changes
        self.name = None
        self._bpm = None
        self._at = 0.0
        self._client = None

    @property
    def connected(self):
        return self._client is not None and self._client.is_connected

    def bpm(self):
        """Latest heart rate, or None if there's no recent reading."""
        return self._bpm if self.connected and time.monotonic() - self._at < FRESH_SECONDS else None

    async def connect(self, timeout=10):
        if self.connected:
            return
        device = await BleakScanner.find_device_by_filter(
            lambda d, adv: HR_SERVICE in adv.service_uuids, timeout=timeout)
        if not device:
            raise HeartRateError("no heart rate strap found - is it on and touching your skin?")
        client = BleakClient(device, disconnected_callback=self._on_disconnect, timeout=timeout)
        await client.connect()
        self._client, self.name = client, device.name
        try:
            await client.start_notify(HR_MEASUREMENT, self._on_measurement)
        except BaseException:
            await self.disconnect()
            raise
        log(f"heart rate strap connected: {self.name}")

    async def disconnect(self):
        client, self._client = self._client, None
        if client:
            await client.disconnect()
            log("heart rate strap disconnected")

    def _on_measurement(self, _, data):
        bpm = parse_heart_rate(bytes(data))
        changed = bpm != self._bpm
        self._bpm, self._at = bpm, time.monotonic()
        if changed and self.on_change:
            self.on_change(bpm)

    def _on_disconnect(self, client):
        if client is self._client:
            log("heart rate strap connection lost")
