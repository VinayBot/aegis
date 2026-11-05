"""Builds the robot assets for the website from the two source pictures in images/.

1. `swift scripts/cutout.swift` lifts each robot off its background (Apple Vision, on-device).
2. This script crops each cutout to its subject, exports AVIF and WebP at several widths, keeps
   the standing robot's starfield as a full-bleed scene, crops the chatbot orb from its head, and
   writes every overlay region (face screen, eyes, tablet) in the cropped image's own pixel space
   to frontend/src/config/robotRegions.json.

    uv run --no-project --with pillow python scripts/build_assets.py
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / "var" / "art"
OUT = ROOT / "frontend" / "public" / "assets"
REGIONS = ROOT / "frontend" / "src" / "config" / "robotRegions.json"

# Measured on the 1276x1136 hover source (see docs/DECISIONS.md, "Robot overlays").
HOVER_CROP = (13, 96, 1201, 942)
HOVER_SCREEN = [
    (521, 252), (749, 252), (806, 271), (835, 291), (852, 311), (864, 350), (862, 390),
    (858, 430), (854, 470), (850, 510), (845, 550), (835, 588), (823, 607), (801, 627),
    (749, 646), (578, 646), (468, 627), (439, 607), (425, 588), (414, 550), (412, 510),
    (415, 470), (418, 430), (423, 390), (430, 350), (444, 311), (458, 291), (480, 271),
]
HOVER_EYES = {"left": (515, 444, 64, 108), "right": (725, 445, 78, 104)}  # cx, cy, w, h
HOVER_MOUTH = (607, 530, 118, 58)
# The live chart sits on the right of the tablet, clear of the hand.
HOVER_TABLET_CHART = [(208, 636), (364, 658), (447, 881), (287, 855)]

# Measured on the 1278x1286 standing source.
STANDING_CROP = (300, 78, 990, 1286)
STANDING_EYES = {"left": (497, 438, 56), "right": (709, 364, 58)}  # cx, cy, ring radius
STANDING_VISOR_COLOUR = (12, 14, 24)
ORB_CROP = (355, 70, 915, 630)
# The flying assistant: head, torso and arms only, feathered out at the waist (a jetpack takes
# over below). Same left/top origin as STANDING_CROP so the eye coordinates carry over.
BOT_CROP = (300, 78, 990, 900)
BOT_FADE_PX = 110

# The "Meet PRAHARI" robot (images/*1.20.15*), measured on its 568x1018 source.
MEET_CROP = (29, 94, 543, 937)
MEET_EYES = {"left": (190.5, 402, 40, 172.5, 396), "right": (381, 402, 40, 398.5, 396)}  # ring cx, cy, r, pupil x, y


def shift(points, crop):
    return [[round(x - crop[0], 1), round(y - crop[1], 1)] for x, y in points]


def save_set(image: Image.Image, stem: str, widths: list[int], quality: int = 70) -> list[dict]:
    variants = []
    for width in widths:
        w = min(width, image.width)
        h = round(image.height * w / image.width)
        resized = image.resize((w, h), Image.LANCZOS)
        for fmt, ext, q in (("AVIF", "avif", quality - 15), ("WEBP", "webp", quality)):
            path = OUT / f"{stem}-{w}.{ext}"
            resized.save(path, fmt, quality=q, method=6) if fmt == "WEBP" else resized.save(
                path, fmt, quality=q
            )
            variants.append({"path": path.name, "width": w, "bytes": path.stat().st_size})
    return variants


def feather_bottom(image: Image.Image, px: int) -> Image.Image:
    """Fade the bottom `px` rows to transparent (multiplies the existing alpha)."""
    img = image.copy()
    alpha = img.getchannel("A")
    w, h = img.size
    ramp = Image.linear_gradient("L").resize((w, px)).transpose(Image.FLIP_TOP_BOTTOM)
    mask = Image.new("L", (w, h), 255)
    mask.paste(ramp, (0, h - px))
    from PIL import ImageChops

    img.putalpha(ImageChops.multiply(alpha, mask))
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    REGIONS.parent.mkdir(parents=True, exist_ok=True)

    hover = Image.open(ART / "hover-cut.png").convert("RGBA").crop(HOVER_CROP)
    standing_cut = Image.open(ART / "standing-cut.png").convert("RGBA")
    standing_scene = Image.open(ART / "standing-src.png").convert("RGB")
    meet = Image.open(ART / "meet-cut.png").convert("RGBA").crop(MEET_CROP)

    report = {
        "hover": save_set(hover, "robot-hover", [480, 760, 1188]),
        "standing": save_set(standing_cut.crop(STANDING_CROP), "robot-standing", [420, 690]),
        "scene": save_set(standing_scene, "robot-standing-scene", [800, 1278], quality=62),
        "orb": save_set(standing_cut.crop(ORB_CROP), "robot-orb", [96, 192]),
        "meet": save_set(meet, "robot-meet", [380, 514]),
        "bot": save_set(feather_bottom(standing_cut.crop(BOT_CROP), BOT_FADE_PX), "robot-bot", [160, 320]),
    }

    hx, hy = HOVER_CROP[0], HOVER_CROP[1]
    regions = {
        "hover": {
            "width": hover.width,
            "height": hover.height,
            "screen": shift(HOVER_SCREEN, HOVER_CROP),
            "eyes": {
                k: {"cx": cx - hx, "cy": cy - hy, "w": w, "h": h}
                for k, (cx, cy, w, h) in HOVER_EYES.items()
            },
            "mouth": {
                "cx": HOVER_MOUTH[0] - hx,
                "cy": HOVER_MOUTH[1] - hy,
                "w": HOVER_MOUTH[2],
                "h": HOVER_MOUTH[3],
            },
            "tablet": shift(HOVER_TABLET_CHART, HOVER_CROP),
        },
        "meet": {
            "width": meet.width,
            "height": meet.height,
            "eyes": {
                k: {"cx": cx - MEET_CROP[0], "cy": cy - MEET_CROP[1], "r": r, "px": px - MEET_CROP[0], "py": py - MEET_CROP[1]}
                for k, (cx, cy, r, px, py) in MEET_EYES.items()
            },
        },
        "standing": {
            "scene": {
                "width": standing_scene.width,
                "height": standing_scene.height,
                "eyes": {k: {"cx": cx, "cy": cy, "r": r} for k, (cx, cy, r) in STANDING_EYES.items()},
                "visor": "#%02x%02x%02x" % STANDING_VISOR_COLOUR,
            },
            "bot": {"width": BOT_CROP[2] - BOT_CROP[0], "height": BOT_CROP[3] - BOT_CROP[1]},
            "cut": {
                "width": STANDING_CROP[2] - STANDING_CROP[0],
                "height": STANDING_CROP[3] - STANDING_CROP[1],
                "eyes": {
                    k: {"cx": cx - STANDING_CROP[0], "cy": cy - STANDING_CROP[1], "r": r}
                    for k, (cx, cy, r) in STANDING_EYES.items()
                },
            },
        },
    }
    REGIONS.write_text(json.dumps(regions, indent=2) + "\n")

    # Pictures of the site's own pages (frontend/tools/screens.mjs) for the tunnel and the reel.
    screens = ROOT / "var" / "screens"
    if screens.exists():
        (OUT / "screens").mkdir(exist_ok=True)
        for png in sorted(screens.glob("*.png")):
            shot = Image.open(png).convert("RGB")
            for w in (480, 800):
                small = shot.resize((w, round(shot.height * w / shot.width)), Image.LANCZOS)
                small.save(OUT / "screens" / f"{png.stem}-{w}.webp", "WEBP", quality=72, method=6)
                small.save(OUT / "screens" / f"{png.stem}-{w}.avif", "AVIF", quality=55)
    for name, variants in report.items():
        for v in variants:
            print(f"{name:9} {v['path']:36} {v['bytes'] / 1024:7.1f} KB")


if __name__ == "__main__":
    main()
