"""Generates assets/text2cad.ico (isometric CAD cube)."""

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "assets" / "text2cad.ico"
S = 256

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([8, 8, S - 8, S - 8], radius=48, fill=(59, 110, 165, 255))

c, r = (S / 2, S / 2 + 6), 78
pt = lambda ang, rad=r: (c[0] + rad * math.cos(math.radians(ang)), c[1] - rad * math.sin(math.radians(ang)))  # noqa: E731
top, left, right, bottom = pt(90), pt(210), pt(330), pt(270)
upper_left, upper_right = pt(150), pt(30)
centre = c
d.polygon([top, upper_right, centre, upper_left], fill=(232, 240, 248, 255))        # top face
d.polygon([upper_left, centre, bottom, left], fill=(170, 195, 220, 255))            # left face
d.polygon([centre, upper_right, right, bottom], fill=(120, 155, 190, 255))          # right face
for a, b in [(top, upper_right), (upper_right, right), (right, bottom), (bottom, left), (left, upper_left),
             (upper_left, top), (upper_left, centre), (upper_right, centre), (centre, bottom)]:
    d.line([a, b], fill=(25, 45, 70, 255), width=6)

OUT.parent.mkdir(exist_ok=True)
img.save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(OUT)
