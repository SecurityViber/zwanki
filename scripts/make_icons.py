"""Draw the app icons into build/icons for PyInstaller: zwanki.icns (macOS), zwanki.ico (Windows), zwanki.png."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from zwanki.icon import draw_icon  # noqa: E402

out = ROOT / "build" / "icons"
out.mkdir(parents=True, exist_ok=True)
# Each size is drawn for itself, so the small ones get the simpler layout instead of a shrunken big one.
draw_icon(1024, margin=0.1).save(
    out / "zwanki.icns", append_images=[draw_icon(s, margin=0.1) for s in (16, 32, 64, 128, 256, 512)])
ico_sizes = (16, 24, 32, 48, 64, 128)
draw_icon(256).save(out / "zwanki.ico", sizes=[(s, s) for s in ico_sizes + (256,)],
                    append_images=[draw_icon(s) for s in ico_sizes])
draw_icon(512).save(out / "zwanki.png")
print(f"icons written to {out}")
