"""The heart of Zwanki: turns controller presses into Anki and trainer actions, and records rides."""

import asyncio
import time
from collections import deque
from datetime import datetime

from bleak import BleakClient, BleakScanner

from . import __version__, anki, claude
from .config import RIDES_PATH, save_config
from .heartrate import HeartRateMonitor
from .kickr import Kickr, TrainerError
from .log import log
from .ride import (
    INDICATE_CHAR, KNOWN_BITS, MSG_BATTERY, MSG_BUTTONS, NOTIFY_CHAR, RIDE_ON, RIDE_SIDES, WRITE_CHAR,
    ZWIFT_COMPANY_ID, Buttons, parse_battery_message, parse_button_message,
)
from .rides import SAMPLE_SECONDS, Recorder, RideStore, describe_setting, summarize

ANKI_POLL_SECONDS = 3
RETRY_SECONDS = 10  # between attempts to find the trainer or heart rate strap
TRAINER_STEP = {"resistance": 5, "grade": 0.5, "power": 10}  # per press of trainer_harder/easier
TRAINER_START = {"resistance": 0, "grade": 0, "power": 100}  # where a mode starts when switched to


class Bridge:
    def __init__(self, config, on_quit=None):
        self.config = config
        self.on_quit = on_quit
        big = config["big_buttons"]
        self.buttons = Buttons(big["press_threshold"], big["release_threshold"])
        self.paused = False
        self.controllers = {side: {"connected": False, "battery": None} for side in RIDE_SIDES.values()}
        self.anki_state = None  # "reviewing", "open" or "offline" once known
        self.history = deque(maxlen=50)
        self.listeners = set()  # one queue per open settings page
        self.presses = asyncio.Queue()
        self.sessions = {}  # address -> task
        self.kickr = Kickr(on_data=self._on_bike_data)
        self.bike_at = 0.0  # when the trainer last sent data
        self.trainer_error = None
        self.heart = HeartRateMonitor(on_change=lambda _: self.emit("heart_rate", self.heart_rate_status()))
        self.heart_rate_error = None
        self.rides = RideStore(RIDES_PATH)
        self.rides.close_abandoned_rides()
        self.recorder = Recorder(self.rides)

    # What the settings page sees.

    def emit(self, event, data):
        for queue in self.listeners:
            if queue.qsize() < 200:  # don't pile up events for a page that stopped reading
                queue.put_nowait((event, data))

    def status(self):
        return {"controllers": self.controllers, "anki": self.anki_state, "paused": self.paused}

    def trainer_status(self):
        k = self.kickr
        return {"connected": k.connected, "name": k.name, "mode": k.mode, "target": k.target,
                "error": self.trainer_error,
                "bike": {key: k.bike.get(key) for key in ("speed_kmh", "cadence_rpm", "power_w")}}

    def heart_rate_status(self):
        h = self.heart
        return {"connected": h.connected, "name": h.name, "bpm": h.bpm(), "error": self.heart_rate_error}

    def rides_overview(self):
        recent = [summarize(r, self.rides.samples(r["id"])) for r in self.rides.rides(limit=8)]
        return {"recording": self.recorder.live(), "rides": recent, "claude": claude.status()}

    def snapshot(self):
        return {"version": __version__, "config": self.config, **self.status(), "trainer": self.trainer_status(),
                "heart_rate": self.heart_rate_status(), "ride": self.recorder.live(),
                "history": list(self.history), "held": sorted(self.buttons.down),
                "pressure": self.buttons.pressure()}

    async def set_config(self, config):
        old = self.config
        old_trainer = old["trainer"]
        save_config(config)
        self.config = config
        big = config["big_buttons"]
        self.buttons.press_at, self.buttons.release_at = big["press_threshold"], big["release_threshold"]
        log("saved settings")
        self.emit("config", config)

        trainer = config["trainer"]
        try:
            if old_trainer["enabled"] and not trainer["enabled"]:
                await self.kickr.disconnect()  # goes back to easy first
            elif trainer["enabled"] and trainer["mode"] != old_trainer["mode"] and self.kickr.connected:
                await self.trainer_set(TRAINER_START[trainer["mode"]])
        except TrainerError as e:
            self.trainer_error = str(e)
        self.emit("trainer", self.trainer_status())
        if old["heart_rate"]["enabled"] and not config["heart_rate"]["enabled"]:
            await self.heart.disconnect()
            self.emit("heart_rate", self.heart_rate_status())

    def set_paused(self, paused):
        self.paused = paused
        log("paused - button presses do nothing" if paused else "resumed - buttons are active")
        self.emit("status", self.status())

    def set_controller(self, side, **changes):
        self.controllers[side].update(changes)
        self.emit("status", self.status())

    def record(self, button, action, result):
        entry = {"time": f"{datetime.now():%H:%M:%S}", "button": button, "action": action, "result": result}
        self.history.append(entry)
        log(f"{button or '?':<16} {action or '-':<15} {result}")
        self.emit("log", entry)

    def quit(self):
        if self.on_quit:
            self.on_quit()

    # Running.

    async def run(self):
        if self.config["general"]["open_anki"]:
            await self.start_anki()
        tasks = [asyncio.create_task(job) for job in (
            self.handle_presses(), self.watch_anki(), self.record_rides(),
            self.keep_connected(self.kickr, "trainer", self.trainer_status),
            self.keep_connected(self.heart, "heart_rate", self.heart_rate_status),
        )]
        log("Looking for Zwift Ride controllers - press a button on each one to wake it up")
        try:
            while True:
                if len(self.sessions) < len(RIDE_SIDES):
                    await self.scan()
                else:
                    await asyncio.sleep(2)
        finally:
            tasks += self.sessions.values()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.finish_ride()
            self.rides.close()
            for device in (self.kickr, self.heart):  # the trainer goes back to easy first
                try:
                    await asyncio.wait_for(device.disconnect(), 10)
                except Exception as e:
                    log(f"disconnecting: {e}")

    async def start_anki(self):
        try:
            await asyncio.to_thread(anki.anki, "version")
        except anki.AnkiError:
            log("starting Anki" if anki.launch_anki() else "couldn't find Anki to start it - please open it yourself")

    async def scan(self):
        found = await BleakScanner.discover(timeout=4, return_adv=True)
        for device, adv in found.values():
            data = adv.manufacturer_data.get(ZWIFT_COMPANY_ID)
            side = RIDE_SIDES.get(data[0]) if data else None
            if side and device.address not in self.sessions:
                task = asyncio.create_task(self.connect(device, side))
                self.sessions[device.address] = task
                task.add_done_callback(lambda _, addr=device.address: self.sessions.pop(addr, None))

    async def connect(self, device, side):
        disconnected = asyncio.Event()
        unknown_bits = 0

        def on_message(_, data):
            nonlocal unknown_bits
            if data[0] == MSG_BUTTONS:
                held_bits, analog = parse_button_message(bytes(data[1:]))
                if held_bits & ~KNOWN_BITS != unknown_bits:
                    unknown_bits = held_bits & ~KNOWN_BITS
                    if unknown_bits:
                        self.record(None, None, f"unknown button (bits 0x{unknown_bits:x}, {side} controller)")
                down, pressure = self.buttons.down, self.buttons.pressure()
                for name in self.buttons.update(side, held_bits, analog):
                    self.presses.put_nowait(name)
                if self.buttons.down != down:
                    self.emit("held", sorted(self.buttons.down))
                if self.buttons.pressure() != pressure:
                    self.emit("pressure", self.buttons.pressure())
            elif data[0] == MSG_BATTERY:
                level = parse_battery_message(bytes(data[1:]))
                if self.controllers[side]["battery"] is None:
                    log(f"{side} controller battery: {level}%")
                if level != self.controllers[side]["battery"]:
                    self.set_controller(side, battery=level)

        try:
            async with BleakClient(device, disconnected_callback=lambda _: disconnected.set()) as client:
                await client.start_notify(NOTIFY_CHAR, on_message)
                await client.start_notify(INDICATE_CHAR, lambda *_: None)
                await client.write_gatt_char(WRITE_CHAR, RIDE_ON, response=False)
                log(f"{side} controller connected")
                self.set_controller(side, connected=True)
                await disconnected.wait()
            log(f"{side} controller disconnected (probably asleep) - will reconnect when it wakes")
        except Exception as e:  # keep the bridge alive whatever goes wrong; the scan loop retries
            log(f"{side} controller: connection failed ({type(e).__name__}: {e}), retrying")
        finally:
            self.buttons.forget(side)
            self.set_controller(side, connected=False)
            self.emit("held", sorted(self.buttons.down))

    async def handle_presses(self):
        while True:
            button = await self.presses.get()
            action = self.config["buttons"].get(button)
            if not action:
                self.record(button, action, "not assigned")
            elif self.paused:
                self.record(button, action, "paused")
            elif action.startswith("trainer_"):
                # The trainer can take seconds to answer or reconnect; don't hold up Anki for it.
                # Kickr runs its commands one at a time, in order.
                asyncio.create_task(self.press_trainer(button, action))
            else:
                try:
                    result = await asyncio.to_thread(anki.perform, action)
                except anki.AnkiError as e:
                    result = f"error: {e}"
                self.record(button, action, result)

    async def press_trainer(self, button, action):
        self.record(button, action, await self.trainer_action(action))

    async def watch_anki(self):
        messages = {"reviewing": "Anki: reviewing", "open": "Anki: open, not reviewing", "offline": "Anki: not reachable"}
        while True:
            try:
                state = "reviewing" if await asyncio.to_thread(anki.anki, "guiReviewActive") else "open"
            except anki.AnkiError:
                state = "offline"
            if state != self.anki_state:
                self.anki_state = state
                log(messages[state])
                self.emit("status", self.status())
            await asyncio.sleep(ANKI_POLL_SECONDS)

    # Trainer, heart rate strap and ride recording.

    async def keep_connected(self, device, section, status):
        """Keep a link to the trainer or heart rate strap while it's enabled; connecting takes seconds.
        section is both its config section and the event the settings page gets."""
        error_attr = f"{section}_error"
        last = None
        while True:
            if self.config[section]["enabled"] and not device.connected:
                error = None
                try:
                    await device.connect(timeout=RETRY_SECONDS)
                except Exception as e:
                    error = str(e)
                    if error != getattr(self, error_attr):
                        log(f"{section.replace('_', ' ')}: {error}")
                setattr(self, error_attr, error)
            now = (device.connected, getattr(self, error_attr))
            if now != last:
                last = now
                self.emit(section, status())
            await asyncio.sleep(RETRY_SECONDS if now[1] else 2)

    def _on_bike_data(self, bike):
        self.bike_at = time.monotonic()
        self.emit("trainer", self.trainer_status())

    async def record_rides(self):
        """Once a second, give the recorder what the trainer and heart rate strap say."""
        while True:
            await asyncio.sleep(SAMPLE_SECONDS)
            if not self.config["rides"]["record"]:
                self.finish_ride()
                continue
            k, h = self.kickr, self.heart
            bike = k.bike if k.connected and time.monotonic() - self.bike_at < 3 else None
            event = self.recorder.sample(time.time(), bike, h.bpm(), (k.mode, k.target), k.name,
                                         h.name if h.connected else None)
            if event == "started":
                log("ride started - recording")
                self.emit("rides", self.rides_overview())
            elif event:
                self.ride_ended(event)
            if self.recorder.ride_id:
                self.emit("ride", self.recorder.live())

    def finish_ride(self):
        event = self.recorder.finish()
        if event:
            self.ride_ended(event)

    def ride_ended(self, event):
        log("ride finished and saved" if event == "finished" else "ride too short to keep")
        self.emit("ride", None)
        self.emit("rides", self.rides_overview())

    async def trainer_set(self, value):
        mode = self.config["trainer"]["mode"]
        setter = {"resistance": self.kickr.set_resistance, "grade": self.kickr.set_grade,
                  "power": self.kickr.set_power}[mode]
        await setter(value)
        return describe_setting(self.kickr.mode, self.kickr.target)

    async def trainer_action(self, action):
        """trainer_harder / trainer_easier / trainer_easy; returns what happened."""
        if not self.config["trainer"]["enabled"]:
            return "the trainer is switched off in settings"
        try:
            if action == "trainer_easy":
                await self.kickr.easy()
                result = "trainer back to easy"
            else:
                mode = self.config["trainer"]["mode"]
                current = self.kickr.target if self.kickr.mode == mode else TRAINER_START[mode]
                step = TRAINER_STEP[mode] if action == "trainer_harder" else -TRAINER_STEP[mode]
                result = await self.trainer_set(current + step)
            self.trainer_error = None
        except TrainerError as e:
            self.trainer_error = str(e)
            result = f"trainer: {e}"
        self.emit("trainer", self.trainer_status())
        return result
