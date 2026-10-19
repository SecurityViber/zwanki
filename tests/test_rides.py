import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from zwanki.heartrate import parse_heart_rate
from zwanki.rides import (IDLE_END_SECONDS, Recorder, RideStore, describe_setting, normalized_power, summarize,
                          weekly_summary)

MONDAY = datetime(2026, 10, 12, 18, 0).timestamp()  # a Monday evening


def ride(recorder, start, seconds, power=200, cadence=90, speed=30, heart_rate=140, setting=("resistance", 20)):
    """Feed the recorder a steady ride, then enough idle time for it to end."""
    for i in range(seconds):
        recorder.sample(start + i, {"power_w": power, "cadence_rpm": cadence, "speed_kmh": speed},
                        heart_rate, setting, "KICKR CORE", "Polar H10")
    t = start + seconds
    while recorder.ride_id:
        recorder.sample(t, {"power_w": 0, "cadence_rpm": 0, "speed_kmh": 0}, heart_rate, setting, "KICKR CORE", "Polar H10")
        t += 1


class RecorderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = RideStore(Path(self.tmp.name) / "rides.db")
        self.recorder = Recorder(self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_no_ride_without_pedaling(self):
        self.recorder.sample(MONDAY, {"power_w": 0, "cadence_rpm": 0}, 70, (None, None), "KICKR", None)
        self.recorder.sample(MONDAY + 1, None, None, (None, None), None, None)
        self.assertEqual(self.store.rides(), [])

    def test_ride_is_recorded_and_trailing_idle_dropped(self):
        ride(self.recorder, MONDAY, 600)
        [r] = self.store.rides()
        s = summarize(r, self.store.samples(r["id"]), self.store.settings(r["id"]))
        self.assertEqual((s["moving_min"], s["elapsed_min"]), (10.0, 10.0))
        self.assertEqual((s["avg_power_w"], s["normalized_power_w"], s["energy_kj"]), (200, 200, 120))
        self.assertEqual((s["avg_heart_rate_bpm"], s["avg_cadence_rpm"], s["distance_km"]), (140, 90, 5.0))
        self.assertEqual(s["minutes_per_trainer_setting"], {"resistance 20%": 10.0})
        self.assertFalse(s["in_progress"])

    def test_short_pause_stays_in_one_ride(self):
        r = self.recorder
        for i in range(120):
            r.sample(MONDAY + i, {"power_w": 150, "cadence_rpm": 80}, None, ("grade", 2), "KICKR", None)
        for i in range(120, 120 + IDLE_END_SECONDS - 10):  # a pause just under the limit
            r.sample(MONDAY + i, {"power_w": 0, "cadence_rpm": 0}, None, ("grade", 2), "KICKR", None)
        ride(r, MONDAY + 120 + IDLE_END_SECONDS - 10, 120, power=150, setting=("grade", 3))
        [only] = self.store.rides()
        s = summarize(only, self.store.samples(only["id"]), self.store.settings(only["id"]))
        self.assertEqual(s["moving_min"], 4.0)
        self.assertEqual([t["setting"] for t in s["trainer_settings"]], ["hill 2%", "hill 3%"])

    def test_tiny_ride_is_not_kept(self):
        ride(self.recorder, MONDAY, 30)
        self.assertEqual(self.store.rides(), [])

    def test_abandoned_ride_is_closed(self):
        for i in range(90):
            self.recorder.sample(MONDAY + i, {"power_w": 100, "cadence_rpm": 70}, None, (None, None), "KICKR", None)
        self.store.close_abandoned_rides()  # what the next start does after a crash
        [r] = self.store.rides()
        self.assertEqual(r["end"], MONDAY + 89)

    def test_weekly_summary(self):
        ride(self.recorder, MONDAY, 1800, power=200, heart_rate=150)
        ride(self.recorder, MONDAY + 86400, 600, power=100, heart_rate=120)
        [week, previous] = weekly_summary(self.store, weeks=2, week_of=date(2026, 10, 18))
        self.assertEqual((week["week"], week["from"], week["to"]), ("2026-W42", "2026-10-12", "2026-10-18"))
        self.assertEqual((week["rides"], week["moving_min"], week["energy_kj"]), (2, 40.0, 420))
        self.assertEqual((week["avg_power_w"], week["avg_heart_rate_bpm"], week["max_heart_rate_bpm"]), (175, 142, 150))
        self.assertEqual(previous["rides"], 0)


class MathTest(unittest.TestCase):
    def test_normalized_power_rewards_variability(self):
        self.assertEqual(normalized_power([200] * 600), 200)
        self.assertGreater(normalized_power([100] * 300 + [300] * 300), 200)
        self.assertIsNone(normalized_power([200] * 10))

    def test_describe_setting(self):
        self.assertEqual(describe_setting("resistance", 15), "resistance 15%")
        self.assertEqual(describe_setting("grade", 1.5), "hill 1.5%")
        self.assertEqual(describe_setting("power", 110), "ERG 110 W")
        self.assertEqual(describe_setting(None, None), "not set by Zwanki")

    def test_heart_rate(self):
        self.assertEqual(parse_heart_rate(bytes([0x16, 72])), 72)  # uint8, contact detected
        self.assertEqual(parse_heart_rate(bytes([0x01, 0x2C, 0x01])), 300)  # uint16
        self.assertIsNone(parse_heart_rate(bytes([0x04, 0])))  # supported but no skin contact
        self.assertIsNone(parse_heart_rate(bytes([0x10, 0])))  # strap lying on the desk


if __name__ == "__main__":
    unittest.main()
