"""Recording rides and summarising them.

While the trainer is connected, Zwanki takes a reading every second. A ride starts when you
pedal and ends after five minutes without pedaling; it's stored in a SQLite database next to
the config, which the MCP server (mcp.py) reads for Claude.
"""

import math
import sqlite3
from datetime import datetime, timedelta

SAMPLE_SECONDS = 1
IDLE_END_SECONDS = 300  # a ride ends after 5 minutes without pedaling
MIN_RIDE_SECONDS = 60  # shorter "rides" (a few turns of the pedals) aren't kept

SCHEMA = """
CREATE TABLE IF NOT EXISTS rides (
    id INTEGER PRIMARY KEY,
    start REAL NOT NULL,              -- unix time
    end REAL,                         -- NULL while recording
    trainer TEXT,
    heart_rate_sensor TEXT
);
CREATE TABLE IF NOT EXISTS samples (
    ride_id INTEGER NOT NULL REFERENCES rides(id) ON DELETE CASCADE,
    t REAL NOT NULL,
    power_w REAL,
    cadence_rpm REAL,
    speed_kmh REAL,
    heart_rate_bpm INTEGER,
    moving INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS samples_by_ride ON samples (ride_id, t);
CREATE TABLE IF NOT EXISTS trainer_settings (
    ride_id INTEGER NOT NULL REFERENCES rides(id) ON DELETE CASCADE,
    t REAL NOT NULL,
    mode TEXT,                        -- resistance, grade or power; NULL = not set by Zwanki
    target REAL
);
"""


class RideStore:
    def __init__(self, path, readonly=False):
        if readonly:
            self.db = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(path)
            self.db.execute("PRAGMA journal_mode = WAL")  # the MCP server reads while we write
            self.db.execute("PRAGMA foreign_keys = ON")
            self.db.executescript(SCHEMA)
        self.db.row_factory = sqlite3.Row

    def close(self):
        self.db.close()

    # Writing (the app).

    def start_ride(self, t, trainer, heart_rate_sensor):
        with self.db:
            return self.db.execute("INSERT INTO rides (start, trainer, heart_rate_sensor) VALUES (?, ?, ?)",
                                   (t, trainer, heart_rate_sensor)).lastrowid

    def add_sample(self, ride_id, t, power_w, cadence_rpm, speed_kmh, heart_rate_bpm, moving):
        with self.db:
            self.db.execute("INSERT INTO samples VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (ride_id, t, power_w, cadence_rpm, speed_kmh, heart_rate_bpm, int(moving)))

    def add_setting(self, ride_id, t, mode, target):
        with self.db:
            self.db.execute("INSERT INTO trainer_settings VALUES (?, ?, ?, ?)", (ride_id, t, mode, target))

    def set_heart_rate_sensor(self, ride_id, name):
        with self.db:
            self.db.execute("UPDATE rides SET heart_rate_sensor = ? WHERE id = ?", (name, ride_id))

    def end_ride(self, ride_id, last_moving):
        """Close a ride at the last moment you were pedaling; drop it if it was too short."""
        with self.db:
            self.db.execute("DELETE FROM samples WHERE ride_id = ? AND t > ?", (ride_id, last_moving))
            moving = self.db.execute("SELECT COUNT(*) FROM samples WHERE ride_id = ? AND moving",
                                     (ride_id,)).fetchone()[0] * SAMPLE_SECONDS
            if moving < MIN_RIDE_SECONDS:
                self.db.execute("DELETE FROM rides WHERE id = ?", (ride_id,))
                return False
            self.db.execute("UPDATE rides SET end = ? WHERE id = ?", (last_moving, ride_id))
            return True

    def close_abandoned_rides(self):
        """Rides left open by a crash: end them at their last pedal stroke."""
        for (ride_id,) in self.db.execute("SELECT id FROM rides WHERE end IS NULL").fetchall():
            last = self.db.execute("SELECT MAX(t) FROM samples WHERE ride_id = ? AND moving", (ride_id,)).fetchone()[0]
            self.end_ride(ride_id, last or 0)

    # Reading (the app's settings page and the MCP server).

    def rides(self, since=None, until=None, limit=None):
        query, args = "SELECT * FROM rides WHERE start >= ? AND start < ? ORDER BY start DESC", [since or 0, until or 1e12]
        if limit:
            query += " LIMIT ?"
            args.append(limit)
        return self.db.execute(query, args).fetchall()

    def ride(self, ride_id):
        return self.db.execute("SELECT * FROM rides WHERE id = ?", (ride_id,)).fetchone()

    def samples(self, ride_id):
        return self.db.execute("SELECT * FROM samples WHERE ride_id = ? ORDER BY t", (ride_id,)).fetchall()

    def settings(self, ride_id):
        return self.db.execute("SELECT * FROM trainer_settings WHERE ride_id = ? ORDER BY t", (ride_id,)).fetchall()


class Recorder:
    """Turns once-a-second readings into rides."""

    def __init__(self, store):
        self.store = store
        self.ride_id = None
        self.started = None
        self.last_moving = None
        self.setting = None
        self.heart_rate_sensor = None
        self._totals = None

    def sample(self, t, bike, heart_rate_bpm, setting, trainer, heart_rate_sensor):
        """bike: latest trainer reading (dict) or None if the trainer isn't sending; setting: (mode, target).
        Returns "started" or "finished" when a ride starts or ends."""
        bike = bike or {}
        power, cadence, speed = bike.get("power_w"), bike.get("cadence_rpm"), bike.get("speed_kmh")
        moving = (cadence or 0) > 0 or (power or 0) > 0
        event = None
        if self.ride_id is None:
            if not moving:
                return None
            self.ride_id = self.store.start_ride(t, trainer, heart_rate_sensor)
            self.started, self.setting, self.heart_rate_sensor = t, None, heart_rate_sensor
            self._totals = {"moving_s": 0, "power": 0.0, "hr": 0, "hr_n": 0}
            event = "started"
        if moving:
            self.last_moving = t
        elif t - self.last_moving > IDLE_END_SECONDS:
            return self.finish()

        if heart_rate_sensor and heart_rate_sensor != self.heart_rate_sensor:
            self.heart_rate_sensor = heart_rate_sensor
            self.store.set_heart_rate_sensor(self.ride_id, heart_rate_sensor)
        if setting != self.setting:
            self.setting = setting
            self.store.add_setting(self.ride_id, t, *setting)
        self.store.add_sample(self.ride_id, t, power, cadence, speed, heart_rate_bpm, moving)
        totals = self._totals
        if moving:
            totals["moving_s"] += SAMPLE_SECONDS
            totals["power"] += (power or 0) * SAMPLE_SECONDS
        if heart_rate_bpm:
            totals["hr"] += heart_rate_bpm
            totals["hr_n"] += 1
        return event

    def finish(self):
        if self.ride_id is None:
            return None
        kept = self.store.end_ride(self.ride_id, self.last_moving)
        self.ride_id = None
        return "finished" if kept else "discarded"

    def live(self):
        """Running totals of the ride being recorded, for the settings page."""
        if self.ride_id is None:
            return None
        t = self._totals
        return {"id": self.ride_id, "start": local_iso(self.started), "moving_min": round(t["moving_s"] / 60, 1),
                "avg_power_w": round(t["power"] / t["moving_s"]) if t["moving_s"] else None,
                "avg_heart_rate_bpm": round(t["hr"] / t["hr_n"]) if t["hr_n"] else None}


# --- summaries ---

def local_iso(t):
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds") if t else None


def describe_setting(mode, target):
    if mode is None:
        return "not set by Zwanki"
    return {"resistance": "resistance {:g}%", "grade": "hill {:g}%", "power": "ERG {:g} W"}[mode].format(target)


def _avg(values):
    return round(sum(values) / len(values), 1) if values else None


def normalized_power(powers):
    """Coggan's normalized power from 1-second power readings: 30 s rolling average, 4th-power mean."""
    window = 30 // SAMPLE_SECONDS
    if len(powers) < window:
        return None
    rolling, total = [], sum(powers[:window])
    rolling.append(total / window)
    for i in range(window, len(powers)):
        total += powers[i] - powers[i - window]
        rolling.append(total / window)
    return round(math.pow(sum(p ** 4 for p in rolling) / len(rolling), 0.25))


def summarize(ride, samples, settings=None):
    """A ride's key numbers. With settings, also how long each trainer setting was ridden."""
    moving = [s for s in samples if s["moving"]]
    powers = [s["power_w"] or 0 for s in samples]

    def present(key, rows, positive=False):
        return [s[key] for s in rows if s[key] is not None and (not positive or s[key] > 0)]

    end = ride["end"] or (samples[-1]["t"] if samples else ride["start"])
    heart = present("heart_rate_bpm", samples, positive=True)
    out = {
        "id": ride["id"],
        "start": local_iso(ride["start"]),
        "end": local_iso(ride["end"]),
        "in_progress": ride["end"] is None,
        "elapsed_min": round((end - ride["start"]) / 60, 1),
        "moving_min": round(len(moving) * SAMPLE_SECONDS / 60, 1),
        "distance_km": round(sum(present("speed_kmh", moving)) * SAMPLE_SECONDS / 3600, 2),
        "energy_kj": round(sum(powers) * SAMPLE_SECONDS / 1000),
        "avg_power_w": _avg([s["power_w"] or 0 for s in moving]),
        "max_power_w": max(present("power_w", samples), default=None),
        "normalized_power_w": normalized_power(powers),
        "avg_cadence_rpm": _avg(present("cadence_rpm", moving, positive=True)),
        "max_cadence_rpm": max(present("cadence_rpm", samples), default=None),
        "avg_speed_kmh": _avg(present("speed_kmh", moving)),
        "max_speed_kmh": max(present("speed_kmh", samples), default=None),
        "avg_heart_rate_bpm": _avg(heart),
        "max_heart_rate_bpm": max(heart, default=None),
        "trainer": ride["trainer"],
        "heart_rate_sensor": ride["heart_rate_sensor"],
    }
    if settings is not None:
        timeline, minutes = [], {}
        for i, s in enumerate(settings):
            until = settings[i + 1]["t"] if i + 1 < len(settings) else end
            label = describe_setting(s["mode"], s["target"])
            timeline.append({"at_min": round((s["t"] - ride["start"]) / 60, 1), "setting": label})
            minutes[label] = round(minutes.get(label, 0) + (until - s["t"]) / 60, 1)
        out["trainer_settings"] = timeline
        out["minutes_per_trainer_setting"] = minutes
    return out


def downsample(ride, samples, seconds):
    """Average the readings over blocks of `seconds`, for a compact picture of the ride."""
    blocks = {}
    for s in samples:
        blocks.setdefault(int((s["t"] - ride["start"]) // seconds), []).append(s)
    out = []
    for n, rows in sorted(blocks.items()):
        out.append({
            "at_min": round(n * seconds / 60, 1),
            "power_w": _avg([r["power_w"] or 0 for r in rows]),
            "cadence_rpm": _avg([r["cadence_rpm"] for r in rows if r["cadence_rpm"] is not None]),
            "speed_kmh": _avg([r["speed_kmh"] for r in rows if r["speed_kmh"] is not None]),
            "heart_rate_bpm": _avg([r["heart_rate_bpm"] for r in rows if r["heart_rate_bpm"]]),
        })
    return out


def week_start(day):
    return day - timedelta(days=day.weekday())


def weekly_summary(store, weeks=4, week_of=None):
    """Totals per Monday-to-Sunday week, newest first, ending with the week containing week_of."""
    last = week_start(week_of or datetime.now().date())
    out = []
    for n in range(weeks):
        first_day = last - timedelta(weeks=n)
        since = datetime.combine(first_day, datetime.min.time()).timestamp()
        until = datetime.combine(first_day + timedelta(days=7), datetime.min.time()).timestamp()
        rides = [summarize(r, store.samples(r["id"])) for r in reversed(store.rides(since, until))]
        moving = sum(r["moving_min"] for r in rides)

        def weighted(key):
            parts = [(r[key], r["moving_min"]) for r in rides if r[key] is not None and r["moving_min"]]
            weight = sum(w for _, w in parts)
            return round(sum(v * w for v, w in parts) / weight) if weight else None

        out.append({
            "week": f"{first_day.isocalendar().year}-W{first_day.isocalendar().week:02d}",
            "from": first_day.isoformat(),
            "to": (first_day + timedelta(days=6)).isoformat(),
            "rides": len(rides),
            "moving_min": round(moving, 1),
            "distance_km": round(sum(r["distance_km"] for r in rides), 1),
            "energy_kj": sum(r["energy_kj"] for r in rides),
            "avg_power_w": weighted("avg_power_w"),
            "avg_heart_rate_bpm": weighted("avg_heart_rate_bpm"),
            "max_heart_rate_bpm": max((r["max_heart_rate_bpm"] for r in rides if r["max_heart_rate_bpm"]), default=None),
            "ride_ids": [r["id"] for r in rides],
            "ride_days": [r["start"][:10] for r in rides],
        })
    return out
