# PyInstaller recipe for the Zwanki app:
#     python scripts/make_icons.py && pyinstaller zwanki.spec
# Output: dist/Zwanki.app on macOS, dist/zwanki/ (zwanki.exe + zwanki-mcp.exe) on Windows,
# dist/zwanki on Linux.
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH)
VERSION = re.search(r'__version__ = "(.+)"', (ROOT / "zwanki" / "__init__.py").read_text()).group(1)
ICONS = ROOT / "build" / "icons"

# bleak and pystray pick their platform backend at runtime, out of PyInstaller's sight.
hidden = collect_submodules("bleak")
hidden.append({"darwin": "pystray._darwin", "win32": "pystray._win32"}.get(sys.platform, "pystray._xorg"))
hidden.append("pystray._dummy")  # for --self-test on machines without a display
if sys.platform == "win32":
    hidden += collect_submodules("winrt")

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "zwanki" / "settings.html"), "zwanki")],
    hiddenimports=hidden,
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Zwanki", console=False)
    coll = COLLECT(exe, a.binaries, a.datas, name="Zwanki")
    app = BUNDLE(
        coll,
        name="Zwanki.app",
        icon=str(ICONS / "zwanki.icns"),
        bundle_identifier="io.github.zwanki",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": "Zwanki",
            "CFBundleShortVersionString": VERSION,
            "LSUIElement": True,  # lives in the menu bar, no Dock icon
            "NSBluetoothAlwaysUsageDescription": "Zwanki connects to your Zwift Ride controllers and smart trainer.",
            "NSHighResolutionCapable": True,
        },
    )
elif sys.platform == "win32":
    # zwanki.exe has no console window (it lives in the tray), and so no usable stdin/stdout.
    # Claude starts the MCP server through zwanki-mcp.exe, the same program as a console app.
    icon = str(ICONS / "zwanki.ico")
    app = EXE(pyz, a.scripts, [], exclude_binaries=True, name="zwanki", console=False, icon=icon)
    mcp = EXE(pyz, a.scripts, [], exclude_binaries=True, name="zwanki-mcp", console=True, icon=icon)
    coll = COLLECT(app, mcp, a.binaries, a.datas, name="zwanki")
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="zwanki", console=True)
