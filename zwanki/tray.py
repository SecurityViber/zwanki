"""The menu bar (macOS) / system tray (Windows, Linux) icon."""

import io
import sys
import threading

from .log import log


def run_tray(open_settings, quit_app, backend):
    """Show the icon until Quit is chosen or the backend stops. Must run on the main thread.
    Returns False if this system has no usable tray (e.g. a Linux desktop without one)."""
    try:
        import pystray

        from .icon import draw_icon, draw_menu_bar_glyph

        def on_quit(icon, _):
            quit_app()
            icon.stop()

        menu = pystray.Menu(
            pystray.MenuItem("Open settings", lambda: open_settings(), default=True),
            pystray.MenuItem("Quit Zwanki", on_quit),
        )
        if sys.platform == "darwin":
            icon = _menu_bar_icon_class(pystray)("zwanki", draw_menu_bar_glyph(36), "Zwanki", menu)
        else:
            icon = pystray.Icon("zwanki", draw_icon(48), "Zwanki", menu)
    except Exception as e:
        log(f"no tray icon ({type(e).__name__}: {e}) - use the settings page")
        return False

    def stop_with_backend():
        backend.join()  # e.g. "Quit" on the settings page
        icon.stop()

    threading.Thread(target=stop_with_backend, daemon=True).start()
    try:
        icon.run()
    except Exception as e:
        log(f"tray icon failed ({type(e).__name__}: {e}) - use the settings page")
        return False
    return True


def _menu_bar_icon_class(pystray):
    import AppKit
    import Foundation

    class MenuBarIcon(pystray.Icon):
        """pystray (0.19) squeezes the image to 1x pixels in full colour. Hand macOS a Retina-sharp
        template image instead, which it tints to match light and dark menu bars."""

        def _assert_image(self):
            if self._icon_image is not None:
                return
            png = io.BytesIO()
            self._icon.save(png, "png")
            image = AppKit.NSImage.alloc().initWithData_(Foundation.NSData(png.getvalue()))
            image.setSize_((self._icon.width / 2, self._icon.height / 2))  # points: drawn at 2x
            image.setTemplate_(True)
            self._icon_image = image
            self._status_item.button().setImage_(image)

    return MenuBarIcon
