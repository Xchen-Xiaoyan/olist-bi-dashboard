"""Crop raw Power BI Desktop window captures down to the report canvas and build a page-tour GIF.

  python scripts/make_screenshots.py <capture_dir>
Expects <capture_dir>/<page>.png (full-resolution window captures); writes docs/screenshots/*.png and
docs/screenshots/tour.gif.
"""
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"
PAGE_BG = (243, 244, 246)  # theme page background (#F3F4F6)
PAGES = ["overview", "growth", "fulfillment", "sellers", "rfm", "retention", "category_detail"]


def canvas_box(img: Image.Image, tol: int = 1) -> tuple[int, int, int, int]:
    """Bounding box of the report canvas: rows/columns made mostly of the page background colour.

    The ribbon's grey is close to the page grey, so the top 17% of the window (ribbon) is skipped.
    """
    rgb = img.convert("RGB")
    w, h = rgb.size
    px = rgb.load()

    def is_bg(x, y):
        r, g, b = px[x, y]
        return abs(r - PAGE_BG[0]) <= tol and abs(g - PAGE_BG[1]) <= tol and abs(b - PAGE_BG[2]) <= tol

    step = 4
    cols = [x for x in range(0, w, step) if sum(is_bg(x, y) for y in range(0, h, 16)) > h / 16 * 0.15]
    rows = [y for y in range(int(h * 0.17), h, step) if sum(is_bg(x, y) for x in range(0, w, 16)) > w / 16 * 0.15]
    return min(cols), min(rows), max(cols) + step, max(rows) + step


def main(capture_dir: Path):
    OUT.mkdir(parents=True, exist_ok=True)
    frames = []
    for name in PAGES:
        src = capture_dir / f"{name}.png"
        if not src.exists():
            print("missing", src)
            continue
        img = Image.open(src)
        crop = img.crop(canvas_box(img))
        crop.save(OUT / f"{name}.png", optimize=True)
        print(f"{name}: {crop.size}")
        if name != "category_detail":
            f = crop.copy()
            f.thumbnail((1200, 1200))
            frames.append(f.convert("P", palette=Image.ADAPTIVE, colors=128))
    if frames:
        frames[0].save(OUT / "tour.gif", save_all=True, append_images=frames[1:], duration=2200, loop=0, optimize=True)
        print(f"tour.gif: {len(frames)} frames, {(OUT / 'tour.gif').stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
