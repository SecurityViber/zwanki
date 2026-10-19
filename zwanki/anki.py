"""Talking to Anki through the AnkiConnect add-on, and starting Anki."""

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ANKI_URL = "http://127.0.0.1:8765"

EASES = {"again": 1, "hard": 2, "good": 3, "easy": 4}
OTHER_ACTIONS = {
    "show_answer": ("guiShowAnswer", "showed answer"),
    "replay_audio": ("guiPlayAudio", "replayed audio"),
    "undo": ("guiUndo", "undid last answer"),
}


class AnkiError(Exception):
    pass


def anki(action, **params):
    body = json.dumps({"action": action, "version": 6, "params": params}).encode()
    try:
        with urllib.request.urlopen(ANKI_URL, body, timeout=5) as resp:
            reply = json.load(resp)
    except OSError as e:
        raise AnkiError("can't reach Anki - is it open, with the AnkiConnect add-on installed?") from e
    if reply["error"]:
        raise AnkiError(reply["error"])
    return reply["result"]


def perform(action):
    """Run one action in Anki and describe what happened."""
    if action in EASES:
        if anki("guiAnswerCard", ease=EASES[action]):
            return f"answered {action.capitalize()}"
        # AnkiConnect refuses to grade while the question is showing, so reveal the answer instead.
        action = "show_answer"
    api, done = OTHER_ACTIONS[action]
    if anki(api):
        return done
    return "Anki isn't reviewing - open a deck and click Study Now"


def launch_anki():
    """Start Anki from where its installer usually puts it. Returns False if it can't be found."""
    try:
        if sys.platform == "darwin":
            return subprocess.run(["open", "-a", "Anki"], capture_output=True).returncode == 0
        if sys.platform == "win32":
            candidates = [
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Anki" / "anki.exe",
                Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Anki" / "anki.exe",
            ]
            exe = next((str(p) for p in candidates if p.is_file()), None)
        else:
            exe = shutil.which("anki")
        if not exe:
            return False
        subprocess.Popen([exe], start_new_session=sys.platform != "win32")
        return True
    except OSError:
        return False
