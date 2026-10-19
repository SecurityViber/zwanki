"""Timestamped log lines, to the terminal (if there is one) and to a log file."""

import sys
from datetime import datetime

_file = None


def start_log_file(path):
    """Start a fresh log file; the packaged app has no terminal to print to."""
    global _file
    path.parent.mkdir(parents=True, exist_ok=True)
    _file = open(path, "w", encoding="utf-8", buffering=1)


def log(msg):
    line = f"{datetime.now():%H:%M:%S}  {msg}"
    if sys.stdout:
        print(line, flush=True)
    if _file:
        _file.write(line + "\n")
