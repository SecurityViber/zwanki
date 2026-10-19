#!/usr/bin/env python3
"""Make a Wahoo KICKR smart trainer harder or easier over Bluetooth LE.

Uses the standard Fitness Machine Service (FTMS), so it should work with most
smart trainers; tested on a KICKR CORE, firmware 3.5.37.

    python -m zwanki.kickr info              # what the trainer supports
    python -m zwanki.kickr resistance 20     # brake at 20% of its range
    python -m zwanki.kickr grade 2           # ride a 2% hill (simulation mode)
    python -m zwanki.kickr power 120         # ERG mode at 120 W
    python -m zwanki.kickr easy              # back to the lightest setting

A setting is ramped up gently and held until Ctrl+C (or --hold seconds), then
the trainer goes back to easy and is disconnected.

From other asyncio code (no asyncio.run needed, nothing blocks):

    kickr = Kickr()
    await kickr.connect()          # optional, commands connect on demand
    await kickr.set_resistance(20)
    await kickr.disconnect()       # back to easy first
"""

import argparse
import asyncio
import math
import struct

from bleak import BleakClient, BleakScanner

from .log import log


def uuid16(n):
    return f"0000{n:04x}-0000-1000-8000-00805f9b34fb"


FTMS_SERVICE = uuid16(0x1826)
FTMS_FEATURE = uuid16(0x2ACC)
INDOOR_BIKE_DATA = uuid16(0x2AD2)
RESISTANCE_RANGE = uuid16(0x2AD6)
POWER_RANGE = uuid16(0x2AD8)
CONTROL_POINT = uuid16(0x2AD9)
MACHINE_STATUS = uuid16(0x2ADA)
DEVICE_INFO = {
    "manufacturer": uuid16(0x2A29), "serial": uuid16(0x2A25),
    "hardware": uuid16(0x2A27), "firmware": uuid16(0x2A26),
}

ZWIFT_COMPANY_ID = 0x094A  # Zwift Ride/Play controllers - never mistake one for the trainer

# Control point op codes, and the result codes in its 0x80 responses
OP_REQUEST_CONTROL = 0x00
OP_SET_RESISTANCE = 0x04
OP_SET_POWER = 0x05
OP_START = 0x07
OP_SIMULATION = 0x11
OP_RESPONSE = 0x80
SUCCESS, CONTROL_NOT_PERMITTED = 0x01, 0x05
RESULTS = {
    0x01: "success", 0x02: "op code not supported", 0x03: "invalid parameter",
    0x04: "operation failed", 0x05: "control not permitted",
}
STATUS_CONTROL_LOST = 0xFF  # Fitness Machine Status: another app took control

# Bits of the Target Setting Features half of the FTMS Feature characteristic
TARGETS = {"resistance": 1 << 2, "power": 1 << 3, "simulation": 1 << 13}

MAX_GRADE = 20.0  # % - FTMS allows far steeper, nobody needs it
RESPONSE_TIMEOUT = 5

# Indoor Bike Data fields after the flags and speed, in order: (flag bit, name, bytes, signed, scale)
BIKE_FIELDS = [
    (0x0002, "avg_speed_kmh", 2, False, 0.01),
    (0x0004, "cadence_rpm", 2, False, 0.5),
    (0x0008, "avg_cadence_rpm", 2, False, 0.5),
    (0x0010, "distance_m", 3, False, 1),
    (0x0020, "resistance", 2, True, 1),
    (0x0040, "power_w", 2, True, 1),
    (0x0080, "avg_power_w", 2, True, 1),
    (0x0100, None, 5, False, 1),  # expended energy, not interesting
    (0x0200, "heart_rate", 1, False, 1),
    (0x0400, None, 1, False, 1),  # metabolic equivalent
    (0x0800, "elapsed_s", 2, False, 1),
    (0x1000, "remaining_s", 2, False, 1),
]


def clamp(value, low, high):
    return max(low, min(high, value))


def parse_bike_data(data):
    """Decode an Indoor Bike Data notification into a dict of the fields it carries."""
    flags = int.from_bytes(data[:2], "little")
    out, i = {}, 2
    if not flags & 0x0001:  # the "More Data" bit is inverted: clear means speed is present
        out["speed_kmh"] = int.from_bytes(data[2:4], "little") * 0.01
        i = 4
    for bit, name, size, signed, scale in BIKE_FIELDS:
        if flags & bit:
            if i + size > len(data):
                break
            if name:
                out[name] = int.from_bytes(data[i:i + size], "little", signed=signed) * scale
            i += size
    return out


class TrainerError(Exception):
    def __init__(self, message, result=None):
        super().__init__(message)
        self.result = result  # control point result code, if the trainer refused a command


class Kickr:
    """One FTMS trainer. Commands are serialised, and reconnect first if the link dropped."""

    def __init__(self, name_prefix="KICKR", address=None, on_data=None, log=log, debug=False):
        self.name_prefix = name_prefix  # None: take any trainer advertising FTMS
        self.address = address
        self.on_data = on_data  # called with self.bike on every Indoor Bike Data notification
        self.log = log
        self.debug = debug  # log every control point write and response
        self.name = None
        self.targets = set()  # what it can be set to: "resistance", "power", "simulation"
        self.resistance_range = (0, 100, 1)  # raw (min, max, increment), read from the trainer
        self.power_range = (0, 2000, 1)  # watts
        self.mode = None  # what we last set: "resistance", "grade" or "power"
        self.target = None  # ...and to what (% of range, % grade, watts)
        self.bike = {}  # latest speed, cadence, power...
        self._client = None
        self._has_control = False
        self._pending = None  # (op code, future) waiting for its response
        self._lock = asyncio.Lock()

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.disconnect()

    @property
    def connected(self):
        return self._client is not None and self._client.is_connected

    async def connect(self, timeout=15):
        """Find the trainer and connect. Does nothing if already connected.
        Control is only taken by the first command, so connecting alone changes nothing."""
        async with self._lock:
            await self._connect(timeout)

    async def disconnect(self, easy=True):
        """Put the trainer back to easy (unless easy=False, or we never set it) and disconnect."""
        if easy and self.connected and self._has_control:
            try:
                await self.easy()
            except Exception as e:
                self.log(f"trainer: couldn't go back to easy before disconnecting ({e})")
        async with self._lock:
            if self._client:
                await self._drop()
                self.log("trainer disconnected")

    async def set_resistance(self, percent):
        """Fixed brake level, 0-100% of the trainer's range. Leaves ERG and simulation mode."""
        await self.connect()  # the range comes from the trainer
        percent = clamp(percent, 0, 100)
        low, high, step = self.resistance_range
        raw = low + round(percent / 100 * (high - low) / step) * step
        # FTMS 1.0 says uint8 here, newer trainers read sint16. Little-endian sint16 suits both
        # while the value is below 256 (the KICKR's range is 0-100 and it accepts either).
        await self._command(struct.pack("<Bh", OP_SET_RESISTANCE, raw))
        self.mode, self.target = "resistance", percent
        return raw

    async def set_grade(self, percent, wind_speed=0.0, crr=0.004, cw=0.51):
        """Simulation mode: ride a hill of this grade (%). Resistance then also grows with speed.
        wind_speed in m/s, crr = rolling resistance coefficient, cw = wind resistance in kg/m."""
        percent = clamp(percent, -MAX_GRADE, MAX_GRADE)
        await self._command(struct.pack(
            "<BhhBB", OP_SIMULATION, round(wind_speed * 1000), round(percent * 100), round(crr * 10000), round(cw * 100)))
        self.mode, self.target = "grade", percent

    async def set_power(self, watts):
        """ERG mode: the trainer holds this power whatever your cadence."""
        await self.connect()
        low, high, _ = self.power_range
        watts = round(clamp(watts, low, high))
        await self._command(struct.pack("<Bh", OP_SET_POWER, watts))
        self.mode, self.target = "power", watts

    async def easy(self):
        """Lightest setting: minimum brake, out of ERG and simulation mode."""
        await self.set_resistance(0)

    async def info(self):
        """Device information and supported ranges, as a dict."""
        await self.connect()
        info = {"name": self.name, "address": self._client.address}
        for key, uuid in DEVICE_INFO.items():
            if self._client.services.get_characteristic(uuid):
                info[key] = (await self._client.read_gatt_char(uuid)).decode(errors="replace")
        info["targets"] = ", ".join(sorted(self.targets))
        info["resistance_range"] = "%d..%d step %d (raw FTMS units)" % self.resistance_range
        info["power_range"] = "%d..%d W step %d" % self.power_range
        return info

    # --- internals ---

    def _looks_like_trainer(self, device, adv):
        if ZWIFT_COMPANY_ID in adv.manufacturer_data:
            return False
        if self.name_prefix:
            return (device.name or adv.local_name or "").upper().startswith(self.name_prefix.upper())
        return FTMS_SERVICE in adv.service_uuids

    async def _connect(self, timeout=15):
        if self.connected:
            return
        if self.address:
            device = await BleakScanner.find_device_by_address(self.address, timeout=timeout)
        else:
            device = await BleakScanner.find_device_by_filter(self._looks_like_trainer, timeout=timeout)
        if not device:
            raise TrainerError("no trainer found - is it powered on and awake?")
        client = BleakClient(device, disconnected_callback=self._on_disconnect, timeout=timeout)
        await client.connect()
        self._client, self.name = client, device.name
        try:
            await self._read_features()
            await client.start_notify(CONTROL_POINT, self._on_response)  # responses come as indications
            await client.start_notify(MACHINE_STATUS, self._on_status)
            await client.start_notify(INDOOR_BIKE_DATA, self._on_bike_data)
        except BaseException:
            await self._drop()
            raise
        self.log(f"trainer connected: {self.name}")

    async def _drop(self):
        client, self._client = self._client, None
        self._has_control = False
        if client:
            await client.disconnect()

    async def _read_features(self):
        c = self._client
        target_bits = struct.unpack("<II", (await c.read_gatt_char(FTMS_FEATURE))[:8])[1]
        self.targets = {name for name, bit in TARGETS.items() if target_bits & bit}
        if c.services.get_characteristic(RESISTANCE_RANGE):
            self.resistance_range = struct.unpack("<hhH", (await c.read_gatt_char(RESISTANCE_RANGE))[:6])
        if c.services.get_characteristic(POWER_RANGE):
            self.power_range = struct.unpack("<hhH", (await c.read_gatt_char(POWER_RANGE))[:6])

    async def _take_control(self):
        await self._send(bytes([OP_REQUEST_CONTROL]))
        try:
            await self._send(bytes([OP_START]))
        except TrainerError as e:  # not every trainer needs or supports it
            self.log(f"trainer: start ignored ({e})")
        self._has_control = True

    async def _command(self, payload):
        async with self._lock:
            await self._connect()
            if not self._has_control:
                await self._take_control()
            try:
                await self._send(payload)
            except TrainerError as e:
                if e.result != CONTROL_NOT_PERMITTED:
                    raise
                # Another app took the trainer; take it back once.
                await self._take_control()
                await self._send(payload)

    async def _send(self, payload):
        """Write one control point command and wait for the trainer's answer."""
        op = payload[0]
        future = asyncio.get_running_loop().create_future()
        self._pending = (op, future)
        if self.debug:
            self.log(f"trainer -> {payload.hex(' ')}")
        try:
            async with asyncio.timeout(RESPONSE_TIMEOUT):
                await self._client.write_gatt_char(CONTROL_POINT, payload, response=True)
                result = await future
        except TimeoutError:
            raise TrainerError(f"no answer to control point op 0x{op:02x}") from None
        finally:
            self._pending = None
        if result != SUCCESS:
            raise TrainerError(f"trainer refused op 0x{op:02x}: {RESULTS.get(result, hex(result))}", result)

    def _on_response(self, _, data):
        if self.debug:
            self.log(f"trainer <- {bytes(data).hex(' ')}")
        if len(data) >= 3 and data[0] == OP_RESPONSE and self._pending:
            op, future = self._pending
            if data[1] == op and not future.done():
                future.set_result(data[2])

    def _on_status(self, _, data):
        if self.debug:
            self.log(f"trainer status {bytes(data).hex(' ')}")
        if data and data[0] == STATUS_CONTROL_LOST:
            self._has_control = False
            self.log("trainer: another app took control")

    def _on_bike_data(self, _, data):
        self.bike.update(parse_bike_data(bytes(data)))
        if self.on_data:
            self.on_data(self.bike)

    def _on_disconnect(self, client):
        if client is not self._client:
            return  # we dropped it on purpose
        self._has_control = False
        if self._pending and not self._pending[1].done():
            self._pending[1].set_exception(TrainerError("trainer disconnected"))
        self.log("trainer connection lost - the next command reconnects")


# --- command line, for trying it by hand ---

STEPS = {"resistance": 5, "grade": 0.5, "power": 25}  # biggest jump per ramp step


def ramp(target, step):
    """Values from just above 0 up to target, so a change is never sudden."""
    n = max(1, math.ceil(abs(target) / step))
    return [target * i / n for i in range(1, n + 1)]


def describe(bike):
    return (f"{bike.get('speed_kmh', 0):5.1f} km/h  {bike.get('cadence_rpm', 0):5.1f} rpm  "
            f"{bike.get('power_w', 0):4.0f} W")


async def run_cli(args):
    async with Kickr(address=args.address, debug=args.debug) as kickr:  # back to easy on exit
        if args.command == "info":
            for key, value in (await kickr.info()).items():
                print(f"{key:>17}: {value}")
            return
        if args.command == "easy":
            await kickr.easy()
            log("trainer set to easy")
            return
        setter = {"resistance": kickr.set_resistance, "grade": kickr.set_grade, "power": kickr.set_power}[args.command]
        for value in ramp(args.value, STEPS[args.command]):
            await setter(value)
            log(f"{args.command} {kickr.target:g}")
            await asyncio.sleep(0.5)
        log("holding - Ctrl+C to stop" if args.hold is None else f"holding for {args.hold:g} s")
        loop = asyncio.get_running_loop()
        end = None if args.hold is None else loop.time() + args.hold
        while end is None or loop.time() < end:
            await asyncio.sleep(2 if end is None else clamp(end - loop.time(), 0, 2))
            log(describe(kickr.bike))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["info", "resistance", "grade", "power", "easy"])
    parser.add_argument("value", type=float, nargs="?", help="%% of range, %% grade, or watts")
    parser.add_argument("--hold", type=float, help="seconds to hold the setting (default: until Ctrl+C)")
    parser.add_argument("--address", help="trainer's Bluetooth address (default: the first KICKR found)")
    parser.add_argument("--debug", action="store_true", help="log every control point command and response")
    args = parser.parse_args()
    if args.command in STEPS and args.value is None:
        parser.error(f"{args.command} needs a value")
    try:
        asyncio.run(run_cli(args))
    except KeyboardInterrupt:
        log("bye")
    except TrainerError as e:
        raise SystemExit(f"error: {e}")


if __name__ == "__main__":
    main()
