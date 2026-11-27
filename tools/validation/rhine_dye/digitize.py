"""Digitize van Mazijk (1996) Fig. 4.4.20 or 4.4.21: the solid squares (Bimmen)
and the + marks (the downstream station). Pixel-to-value from the frame:
t = 5 + (x - X5) / PXDAY days, phi = (Y0 - y) / PX005 * 0.05 ug/l."""
import json
import sys

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

path, out_png, out_json = sys.argv[1], sys.argv[2], sys.argv[3]
X5, X11, Y0, PX005 = (float(v) for v in sys.argv[4:8])
PXDAY = (X11 - X5) / 6.0
box = (int(X5) + 3, int(Y0 - 7 * PX005) - 12, int(X11) - 3, int(Y0) + 12)  # inside the frame

img = Image.open(path).convert("L")
a = np.asarray(img, dtype=float)
x_lo, y_lo, x_hi, y_hi = box
sub = a[y_lo:y_hi, x_lo:x_hi] < 120

# Squares: what survives an opening by a 6x6 block (thin lines and + strokes do not).
sq = ndimage.binary_opening(sub, structure=np.ones((6, 6)))
lab, n = ndimage.label(sq)
squares = []
for sl in ndimage.find_objects(lab):
    h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
    cy = (sl[0].start + sl[0].stop) / 2
    if h < 5:
        continue
    k = max(1, round(w / 14.0))  # a run of touching squares: split evenly
    for j in range(k):
        cx = sl[1].start + (j + 0.5) * w / k
        squares.append((x_lo + cx, y_lo + cy))

# + marks: a vertical and a horizontal stroke crossing, each at least 13 px of ink
# through the point, away from the squares.
not_sq = sub & ~ndimage.binary_dilation(sq, iterations=3)
v = ndimage.uniform_filter1d(not_sq.astype(float), 13, axis=0) * 13
hz = ndimage.uniform_filter1d(not_sq.astype(float), 13, axis=1) * 13
cand = (v >= 11) & (hz >= 11) & not_sq
lab2, n2 = ndimage.label(ndimage.binary_dilation(cand, iterations=2))
crosses = [(x_lo + c[1], y_lo + c[0]) for c in ndimage.center_of_mass(cand, lab2, range(1, n2 + 1))]

to_val = lambda x, y: (round(5 + (x - X5) / PXDAY, 4), round((Y0 - y) / PX005 * 0.05, 5))  # noqa: E731
res = {"squares": sorted(to_val(*p) for p in squares), "crosses": sorted(to_val(*p) for p in crosses)}
json.dump(res, open(out_json, "w"))
draw = ImageDraw.Draw(img := img.convert("RGB"))
for x, y in squares:
    draw.rectangle([x - 8, y - 8, x + 8, y + 8], outline=(255, 0, 0), width=2)
for x, y in crosses:
    draw.ellipse([x - 9, y - 9, x + 9, y + 9], outline=(0, 160, 255), width=2)
img.crop((x_lo - 40, y_lo - 20, x_hi + 20, y_hi + 30)).save(out_png)
print(len(squares), "squares,", len(crosses), "crosses")
