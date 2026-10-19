"""Starts Zwanki: Bluetooth and the settings server run in a background thread, the tray icon on
the main thread (macOS insists on that)."""

import argparse
import asyncio
import importlib
import importlib.util
import os
import sys
import threading
import webbrowser

from . import __version__
from .bridge import Bridge
from .config import CONFIG_PATH, LOG_PATH, SETTINGS_URL, load_config
from .log import log, start_log_file
from .server import PAGE, SettingsServer
from .tray import run_tray


class Backend(threading.Thread):
    """Runs the bridge and the settings server in their own event loop."""

    def __init__(self, config):
        super().__init__(name="zwanki-backend", daemon=True)
        self.config = config
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.port_taken = False
        self._main_task = None
        self._stopping = False

    def run(self):
        self.loop.run_until_complete(self._main())

    async def _main(self):
        self._main_task = asyncio.current_task()
        bridge = Bridge(self.config, on_quit=self.stop)
        try:
            await SettingsServer(bridge).start()
        except OSError:
            self.port_taken = True
            return
        finally:
            self.ready.set()
        if self._stopping:
            return
        try:
            await bridge.run()
        except asyncio.CancelledError:
            pass  # stop() was called; bridge.run() has cleaned up

    def stop(self):
        """Callable from any thread."""
        self._stopping = True
        if self._main_task and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._main_task.cancel)


def open_settings():
    webbrowser.open(SETTINGS_URL)


def self_test():
    """For CI, on the packaged app: are the pieces that are only imported at runtime bundled?"""
    try:
        bleak_backend = {"darwin": "corebluetooth", "win32": "winrt"}.get(sys.platform, "bluezdbus")
        for module in ("aiohttp.web", "PIL.Image", "zwanki.bridge", f"bleak.backends.{bleak_backend}.client"):
            importlib.import_module(module)
        tray_backend = {"darwin": "pystray._darwin", "win32": "pystray._win32"}.get(sys.platform, "pystray._xorg")
        os.environ["PYSTRAY_BACKEND"] = "dummy"  # importing pystray must not need a display (CI has none)
        if importlib.util.find_spec(tray_backend) is None:
            raise ImportError(f"{tray_backend} is missing")
        if not PAGE.is_file():
            raise FileNotFoundError(PAGE)
    except Exception as e:  # a windowed Windows build would show a dialog for an uncaught error
        print(f"self-test failed: {type(e).__name__}: {e}")
        sys.exit(1)
    print(f"zwanki {__version__} self-test ok")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="zwanki", description="Review Anki cards and steer a smart trainer with Zwift Ride controllers.")
    parser.add_argument("--settings", action="store_true", help="open the settings page in your browser")
    parser.add_argument("--no-tray", action="store_true", help="run without the menu bar / tray icon")
    parser.add_argument("--mcp", action="store_true", help="serve your rides to Claude (MCP over stdin/stdout)")
    parser.add_argument("--version", action="version", version=f"zwanki {__version__}")
    parser.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.mcp:
        from .mcp import serve  # no logging here: stdout belongs to the MCP protocol
        return serve()

    start_log_file(LOG_PATH)
    config, first_run, problem = load_config()
    if problem:
        log(f"warning: {problem}")
    backend = Backend(config)
    backend.start()
    backend.ready.wait()
    if backend.port_taken:
        log("Zwanki is already running - opening its settings page")
        open_settings()
        return
    log(f"Zwanki {__version__} - settings: {SETTINGS_URL} (saved in {CONFIG_PATH})")
    if args.settings or first_run:
        open_settings()

    try:
        if args.no_tray or not run_tray(open_settings, backend.stop, backend):
            while backend.is_alive():
                backend.join(0.5)  # wake up regularly so Ctrl+C works
    except KeyboardInterrupt:
        pass
    finally:
        backend.stop()
        backend.join(15)  # time to put the trainer back to easy
    log("bye")
