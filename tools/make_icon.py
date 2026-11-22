"""Program / extension icon from the mascot art (tools/art/mascot.jpg: the whale maid girl).

    .venv/Scripts/python tools/make_icon.py   # -> app/web/assets/icon.ico, icon-256.png, extension/icons/

Head-and-shoulders crop; the white paper background is replaced (flood fill from the edges, so white
parts of the drawing such as the apron and eyes stay) by a deep-sea blue gradient in a rounded square.
"""
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent     # repository root
SRC = ROOT / "tools" / "art" / "mascot.jpg"
ASSETS = ROOT / "app" / "web" / "assets"
CROP = (196, 30, 756, 590)          # square around the head (source is 941 x 1672)
TIGHT = (300, 150, 652, 502)        # face only: for 16-32 px, where the whole head would be a blur
S = 512


def cutout(img: Image.Image) -> Image.Image:
    """Alpha mask: everything connected to the border that is near-white becomes transparent."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    marker = (255, 0, 255)
    work = rgb.copy()
    for seed in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0), (0, h // 2), (w - 1, h // 2)]:
        if sum(work.getpixel(seed)) > 690:
            ImageDraw.floodfill(work, seed, marker, thresh=40)
    diff = ImageChops.difference(work, Image.new("RGB", work.size, marker)).convert("L")
    mask = diff.point(lambda v: 255 if v > 24 else 0).filter(ImageFilter.GaussianBlur(1.2))
    out = rgb.convert("RGBA")
    out.putalpha(mask)
    return out


def draw(crop=CROP) -> Image.Image:
    art = cutout(Image.open(SRC).crop(crop)).resize((S, S), Image.LANCZOS)
    bg = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(bg)
    top, bot = (0x7d, 0xb8, 0xff), (0x1c, 0x2f, 0x7a)          # sky blue -> deep sea
    for y in range(S):
        t = y / (S - 1)
        gd.line([(0, y), (S, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bot)) + (255,))
    # soft light behind the head
    glow = Image.new("L", (S, S), 0)
    ImageDraw.Draw(glow).ellipse([S * 0.12, S * 0.02, S * 0.88, S * 0.78], fill=110)
    bg = Image.composite(Image.new("RGBA", (S, S), (255, 255, 255, 255)), bg, glow.filter(ImageFilter.GaussianBlur(S * 0.08)))
    bg.alpha_composite(art)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.23), fill=255)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(bg, (0, 0), mask)
    return out


def main():
    img, face = draw(), draw(TIGHT)
    small = [face.resize((n, n), Image.LANCZOS) for n in (16, 24, 32)]
    img.save(ASSETS / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
             append_images=small)
    img.resize((256, 256), Image.LANCZOS).save(ASSETS / "icon-256.png")
    out = ROOT / "extension" / "icons"
    out.mkdir(exist_ok=True)
    for n in (16, 32, 48, 128):
        (face if n <= 32 else img).resize((n, n), Image.LANCZOS).save(out / f"icon{n}.png")
    print("wrote app/web/assets/icon.ico, icon-256.png and extension/icons/")


if __name__ == "__main__":
    main()
