"""Build Blaine's profile-page hero from 'ripped blaine.png'.

TWO STRAIGHT SIDES. The source is torn on all four edges. The page wants it
flush into the panel: top, left and bottom cut straight so the photo bleeds to
the card edge, and ONLY the right edge left ragged, where it breaks into the
copy column. So: key the cream matte to alpha, then crop past the tear on
three sides and stop short of it on the fourth.

The matte is keyed by a flood fill from the border inward, NOT a global colour
key: the poster's interior has its own light pixels (the OSU marks, teeth, the
sleeve lettering) and a global key would punch holes straight through them. A
connected fill can only eat background that actually touches the border, and
the black brush frame stops it.
"""
import numpy as np
from collections import deque
from PIL import Image

SRC = r"output/personas/Fat friends/Fat/ripped blaine.png"
OUT = r"docs/assets/profiles/panel/blaine-ripped.webp"
LONG_EDGE = 1200
TOL = 34
OPAQUE = 250

im = Image.open(SRC).convert("RGB")
a = np.asarray(im).astype(np.int16)
h, w, _ = a.shape

frame = np.concatenate([
    a[:3, :, :].reshape(-1, 3), a[-3:, :, :].reshape(-1, 3),
    a[:, :3, :].reshape(-1, 3), a[:, -3:, :].reshape(-1, 3),
])
matte = np.median(frame, axis=0)
dist = np.abs(a - matte).max(axis=2)
cand = dist <= TOL

out = np.zeros((h, w), dtype=bool)
q = deque()
for x in range(w):
    for y in (0, h - 1):
        if cand[y, x] and not out[y, x]:
            out[y, x] = True
            q.append((y, x))
for y in range(h):
    for x in (0, w - 1):
        if cand[y, x] and not out[y, x]:
            out[y, x] = True
            q.append((y, x))
while q:
    y, x = q.popleft()
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        ny, nx = y + dy, x + dx
        if 0 <= ny < h and 0 <= nx < w and cand[ny, nx] and not out[ny, nx]:
            out[ny, nx] = True
            q.append((ny, nx))

alpha = np.where(out, 0.0, 255.0)
d = dist.astype(np.float32)
edge = (~out) & (d <= TOL + 26)
alpha[edge] = (np.clip((d - TOL) / 26.0, 0, 1) * 255.0)[edge]
solid = alpha >= OPAQUE

# --- where to cut the three straight sides -------------------------------
# Per row/column, how far in does the tear reach before the photo goes solid?
# The 96th percentile, not the max: the tear throws isolated speckles well
# past its own body, and cutting to the deepest one would eat a visible slab
# of the picture to chase a handful of pixels.
def first_solid(arr):           # per row: first solid x
    idx = arr.argmax(axis=1).astype(float)
    idx[~arr.any(axis=1)] = np.nan
    return idx

# MEASURE EACH SIDE ONLY WHERE THE OTHER SIDES' TEARS DO NOT REACH. Scanning
# the bottom edge across the full width walks up through the right-hand tear
# for every column inside it, which reported a 387px-deep "bottom tear" that
# is really the right one seen end-on — and cropping to it turned a portrait
# into a landscape. Two passes: rough depths, then re-measure each side inside
# the window the rough pass proves is clear.
def depths(win_rows, win_cols):
    L = np.nanpercentile(first_solid(solid[win_rows, :]), 96)
    R = np.nanpercentile(first_solid(solid[win_rows, ::-1]), 96)
    T = np.nanpercentile(first_solid(solid.T[win_cols, :]), 96)
    B = np.nanpercentile(first_solid(solid.T[win_cols, ::-1]), 96)
    return L, R, T, B

L, R, T, B = depths(slice(None), slice(None))
for _ in range(3):
    rows = slice(int(T) + 10, h - int(B) - 10)
    cols = slice(int(L) + 10, w - int(R) - 10)
    L, R, T, B = depths(rows, cols)
left, right_in, top, bottom_in = L, R, T, B
print("tear depth  L%.0f T%.0f B%.0f  (R kept ragged, depth %.0f)"
      % (left, top, bottom_in, right_in))

# SOLVE each straight cut directly rather than nudging inward from the
# percentile. Nudging optimises the wrong thing: it stops at the first cut
# that passes, which depends entirely on where it started, and tightening the
# pass mark to chase a clean edge walked the bottom 50px up and squared the
# picture off at his waist. What we actually want is the OUTERMOST line on
# each side that is still solid — maximum picture, genuinely straight edge.
# 0.92, chosen by eye against three candidates. Requiring a 99%-solid row
# forced the bottom cut up to his waist to avoid a few deep notches; at 0.92
# the picture keeps his arms and hands and the remaining nicks are invisible,
# because the matte behind them (#f3ede4) and the paper they sit on (#f5f0e7)
# are within three values of each other. Raise it and you lose picture; lower
# it and the tear starts showing along edges that are supposed to read
# straight.
SOLID_FRAC = 0.92
x1 = w                                   # RIGHT EDGE: keep the tear
xr = x1 - int(right_in) - 8              # a straight edge only has to be
                                         # solid up to where the tear begins

def scan(candidates, ok):
    for v in candidates:
        if ok(v):
            return v
    return candidates[-1]

# Provisional window so each side's test is not itself contaminated.
py0, py1 = int(top), h - int(bottom_in)
x0 = scan(range(0, w // 3),
          lambda x: (alpha[py0:py1, x] >= OPAQUE).mean() >= SOLID_FRAC)
y0 = scan(range(0, h // 3),
          lambda y: (alpha[y, x0:xr] >= OPAQUE).mean() >= SOLID_FRAC)
y1 = scan(range(h - 1, h * 2 // 3, -1),
          lambda y: (alpha[y, x0:xr] >= OPAQUE).mean() >= SOLID_FRAC) + 1
print("crop box:", (x0, y0, x1, y1), "->", (x1 - x0, y1 - y0))

rgba = np.dstack([np.asarray(im).astype(np.uint8), alpha.astype(np.uint8)])
img = Image.fromarray(rgba, "RGBA").crop((x0, y0, x1, y1))

scale = LONG_EDGE / max(img.size)
if scale < 1:
    img = img.resize((round(img.width * scale), round(img.height * scale)),
                     Image.LANCZOS)
img.save(OUT, "WEBP", quality=88, method=6)

al = np.asarray(img)[:, :, 3]
print("wrote", OUT, img.size, "aspect %.3f" % (img.width / img.height))
xr2 = int(al.shape[1] * (1 - right_in / (x1 - x0))) - 6
print("edge opacity  left %.3f  top %.3f  bottom %.3f  (measured left of the tear)"
      % ((al[:, 0] >= OPAQUE).mean(), (al[0, :xr2] >= OPAQUE).mean(),
         (al[-1, :xr2] >= OPAQUE).mean()))
print("right edge is ragged: %.0f%% of its column is transparent"
      % (100.0 * (al[:, -1] < 8).mean()))
