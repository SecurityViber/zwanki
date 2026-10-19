"""Stamp a release version into the app before building: python scripts/set_version.py 1.2.3"""

import re
import sys
from pathlib import Path

version = sys.argv[1].lstrip("v")
if not re.fullmatch(r"\d+\.\d+\.\d+([.-][0-9A-Za-z.-]+)?", version):
    sys.exit(f"{version!r} isn't a version like 1.2.3")
init = Path(__file__).resolve().parent.parent / "zwanki" / "__init__.py"
text, count = re.subn(r'__version__ = ".*"', f'__version__ = "{version}"', init.read_text())
if count != 1:
    sys.exit(f"no __version__ line in {init}")
init.write_text(text)
print(f"version set to {version}")
