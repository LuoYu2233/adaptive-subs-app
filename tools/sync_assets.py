"""Keep the player and unpacked extension on the same learning rules."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def main():
    for name in ("learning.js", "profile-sync.js"):
        source = ROOT / "shared" / name
        for folder in (ROOT / "app/web/assets", ROOT / "extension"):
            shutil.copyfile(source, folder / name)


if __name__ == "__main__":
    main()
