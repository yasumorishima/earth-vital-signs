"""Overlay the real Mauna Loa CO2 curve (30-day mean of data/co2_daily_mauna_loa.csv) on the cover art."""
import sys

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFilter

src, csv, out = sys.argv[1:4]
base = Image.open(src).convert("RGB")
W, H = base.size
S = 2  # supersample for smooth lines
w, h = W * S, H * S

co2 = pd.read_csv(csv, parse_dates=["date"]).set_index("date")["co2_ppm"]
co2 = co2.asfreq("D").interpolate(limit=60).rolling(30, center=True, min_periods=10).mean().dropna()
t = (co2.index - co2.index[0]).days.values.astype(float)
t /= t[-1]
v = (co2.values - co2.values.min()) / (co2.values.max() - co2.values.min())

# placement: rises from lower left to upper right, staying above the globe
x0, x1 = 0.07 * w, 0.93 * w
yb, yt = 0.39 * h, 0.06 * h
xs = x0 + t * (x1 - x0)
ys = yb - v * (yb - yt)
pts = list(zip(xs, ys))


def colour(f):
    """cool white-blue (1974) -> warm orange-red (today)"""
    a = np.array([200, 225, 255])
    b = np.array([255, 170, 60])
    c = np.array([255, 80, 50])
    rgb = a + (b - a) * min(f / 0.6, 1) if f < 0.6 else b + (c - b) * (f - 0.6) / 0.4
    return tuple(int(x) for x in rgb)


def stroke(width, alpha):
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    step = 6
    for i in range(0, len(pts) - step, step):
        d.line(pts[i:i + step + 1], fill=colour(i / len(pts)) + (alpha,), width=width, joint="curve")
    return layer


canvas = base.resize((w, h), Image.LANCZOS).convert("RGBA")
for width, alpha, blur in [(90, 120, 50), (36, 180, 18), (16, 235, 6)]:
    canvas = Image.alpha_composite(canvas, stroke(width, alpha).filter(ImageFilter.GaussianBlur(blur)))
canvas = Image.alpha_composite(canvas, stroke(8, 255))

# bright dot at today's value
d = ImageDraw.Draw(canvas)
ex, ey = pts[-1]
for r, a in [(40, 60), (22, 140), (10, 255)]:
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([ex - r, ey - r, ex + r, ey + r], fill=(255, 120, 60, a))
    canvas = Image.alpha_composite(canvas, glow.filter(ImageFilter.GaussianBlur(r / 2)))
ImageDraw.Draw(canvas).ellipse([ex - 7, ey - 7, ex + 7, ey + 7], fill=(255, 245, 230, 255))

canvas.resize((W, H), Image.LANCZOS).convert("RGB").save(out, quality=95)
print(out, W, H, f"{co2.index[0].date()}..{co2.index[-1].date()}", f"{co2.min():.1f}-{co2.max():.1f} ppm")
