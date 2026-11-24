"""Keep the player and unpacked extension on the same learning rules."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT / "shared" / "learning.js"
    for destination in (ROOT / "app/web/assets/learning.js", ROOT / "extension/learning.js"):
        shutil.copyfile(source, destination)


if __name__ == "__main__":
    main()
