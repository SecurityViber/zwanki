"""Settings: where they live, their defaults, and reading/writing config.toml."""

import copy
import json
import os
import sys
import tomllib
from pathlib import Path

from .ride import BUTTON_NAMES

SETTINGS_PORT = 8770
SETTINGS_URL = f"http://127.0.0.1:{SETTINGS_PORT}"

ANKI_ACTIONS = ("show_answer", "again", "hard", "good", "easy", "replay_audio", "undo")
TRAINER_ACTIONS = ("trainer_harder", "trainer_easier", "trainer_easy")
ACTIONS = ANKI_ACTIONS + TRAINER_ACTIONS
TRAINER_MODES = ("resistance", "grade", "power")

DEFAULTS = {
    "general": {
        "open_anki": True,  # start Anki along with Zwanki
    },
    "buttons": {
        "right_big": "show_answer",
        "left_big": "replay_audio",
        "right_up": "again",
        "left_up": "hard",
        "right_down": "good",
        "left_down": "easy",
        "a": "good",
        "b": "hard",
        "y": "easy",
        "z": "again",
        "dpad_right": "show_answer",
        "dpad_up": "replay_audio",
    },
    "big_buttons": {
        "press_threshold": 60,  # how hard (0-100) a big button must be pressed to count
        "release_threshold": 25,  # how far it must come back before it can fire again
    },
    "trainer": {
        "enabled": False,
        "mode": "resistance",  # what trainer_harder/easier change: resistance, grade or power
    },
    "heart_rate": {
        "enabled": False,  # read a Bluetooth heart rate strap
    },
    "rides": {
        "record": True,  # record rides (for Claude, through `zwanki --mcp`)
    },
}


def config_dir():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Zwanki"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Zwanki"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "zwanki"


CONFIG_PATH = config_dir() / "config.toml"
LOG_PATH = config_dir() / "zwanki.log"
RIDES_PATH = config_dir() / "rides.db"


def parse_config(cfg):
    """Validate a config (from config.toml or the settings page); returns it with defaults filled in."""
    if not isinstance(cfg, dict):
        raise ValueError("expected a table of settings")
    out = {}
    for section, defaults in DEFAULTS.items():
        value = cfg.get(section, defaults)
        if not isinstance(value, dict):
            raise ValueError(f"[{section}] must be a table")
        if section != "buttons":
            unknown = value.keys() - defaults.keys()
            if unknown:
                raise ValueError(f"[{section}] has unknown setting(s): {', '.join(sorted(unknown))}")
            value = {**defaults, **value}
        out[section] = copy.deepcopy(value)

    for button, action in out["buttons"].items():
        if button not in BUTTON_NAMES:
            raise ValueError(f"unknown button {button!r}")
        if not isinstance(action, str) or action not in ACTIONS:
            raise ValueError(f"unknown action {action!r} for {button}")
    big = out["big_buttons"]
    press_at, release_at = big["press_threshold"], big["release_threshold"]
    if type(press_at) is not int or type(release_at) is not int or not 0 < release_at < press_at <= 100:
        raise ValueError("big_buttons needs whole numbers with 0 < release_threshold < press_threshold <= 100")
    for section, key in (("general", "open_anki"), ("trainer", "enabled"), ("heart_rate", "enabled"), ("rides", "record")):
        if not isinstance(out[section][key], bool):
            raise ValueError(f"{section}.{key} must be true or false")
    if out["trainer"]["mode"] not in TRAINER_MODES:
        raise ValueError(f"trainer.mode must be one of {', '.join(TRAINER_MODES)}")
    return out


def load_config(path=CONFIG_PATH):
    """Returns (config, first_run, problem). A missing file is created with the defaults; a broken
    one is set aside as config.toml.broken so the app still starts."""
    if not path.exists():
        save_config(DEFAULTS, path)
        return copy.deepcopy(DEFAULTS), True, None
    try:
        with open(path, "rb") as f:
            return parse_config(tomllib.load(f)), False, None
    except ValueError as e:  # TOMLDecodeError is a ValueError too
        path.replace(path.with_name(path.name + ".broken"))
        save_config(DEFAULTS, path)
        return copy.deepcopy(DEFAULTS), False, f"{path.name} was invalid ({e}); started over with the defaults"


def _toml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(value)  # our strings are plain, and a JSON string is a valid TOML string


def save_config(cfg, path=CONFIG_PATH):
    lines = [
        "# Zwanki settings. Written by the settings page"
        f" ({SETTINGS_URL}); hand edits work too, but restart Zwanki after making them.",
        f"# Actions: {', '.join(ACTIONS)}",
    ]
    for section, values in cfg.items():
        lines += ["", f"[{section}]"] + [f"{key} = {_toml_value(value)}" for key, value in values.items()]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)
