"""
dice_vision.py
--------------
The webcam half of the craps tracker. dice_tracker_v2.py imports this file,
so keep the two files in the same folder.

It runs the camera in the background, waits for the dice to land and stop,
reads them, and reports each throw.

How one die is read:
  1. Find the green blobs (the dice).
  2. Straighten each die's top face into a small square picture (64 x 64).
  3. Find the white pips in that square.
  4. Check that the pips form a real die layout: a 5 must be four corners plus
     the centre, a 6 two rows of three, and so on. Merged pips, missing pips
     and glare spots usually break the layout.
  5. If the layout check fails and a trained model exists (dice_model.npz, made
     by train_dice_model.py from your own saved throws), the model gets a say.
     Otherwise the die is marked unreadable ("?") rather than guessed.

Needs:  pip install opencv-python numpy
"""

import base64
import json
import os
import queue
import sys
import threading
import time
from collections import Counter, deque
from datetime import datetime

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

# ------------------------------------------------------------------ settings
HUE_LOW, HUE_HIGH = 35, 100     # which greens count as "die"
SAT_LOW = 10                    # raise if the carpet speckles the mask, lower if the dice are pale
VAL_LOW = 15                    # raise if dark patches show up, lower if the dice are very dark
MIN_DIE_AREA = 3500              # smaller green blobs are ignored (for a 1920-wide picture)
TWO_DICE_AREA = 7000            # a blob this big (1920-wide picture) is two touching dice; one die is under about 6000
SPLIT_ASPECT = 1.6              # a blob this much longer than wide is treated as two touching dice
WHITE_LEVEL = 180              # how bright a spot must be to count as a pip
FELT_HUE = None                 # the felt's hue (OpenCV's 0-179; red felt is about 172). When set, a die washed out by
                                # glare (pale, hardly green) is still found: a die-sized spot that isn't felt, with felt
                                # all round it. None looks for green only
FELT_HUE_RANGE = 20             # how far from FELT_HUE still counts as felt, and
FELT_SAT_LOW = 50               # how pale it may be: sun through a window shade lights the felt in die-sized spots,
                                # brighter, paler and a little pinker, and they must stay felt. The bleached die is
                                # paler still and turns the other way, towards orange
FELT_VAL_LOW = 60               # and not dark: a black laptop beside the felt looks dark red (a die's shadow is above 80)

EXPOSURE = -2                   # camera brightness; None lets the camera choose. Each step darker doubles the frame rate
EXPOSURE_LINUX = None           # the same on Linux, in tenths of a millisecond; None works it out from EXPOSURE
GAIN = None                     # brightens a short exposure back up, with a little noise (Brio: 0-255); None keeps the camera's own
WHITE_BALANCE = None            # colour temperature in kelvin with automatic white balance off (Brio: 2000-7500). Left
                                # automatic, the colours shift with what fills the picture: with the felt filling it the
                                # dice came out yellow and weren't found. None keeps the camera's own
FOCUS = None                    # lens focus with autofocus off (Brio: 0-255, lower = farther away); None keeps the camera's own
FRAME_WIDTH, FRAME_HEIGHT = 1920, 1080
NADIR = None                    # [x, y] of the spot straight under the camera, in the picture as read (turned by ROTATE)
TILT = None                     # how wide a die's side face shows, per pixel from NADIR (see top_face); None = all top
ROTATE = 0                      # turn each picture this many degrees clockwise (0, 90, 180, 270) before reading it, for
                                # a camera mounted on its side to see more of a long table: turned, the shooter's
                                # left is the picture's left again, and "left die first" still holds

STILL_SECONDS = 0.35            # how long the dice must sit still before the throw is reported
WARMUP_SECONDS = 1.5            # nothing is read this soon after the camera starts: the lens is still moving to FOCUS
READ_AFTER = 0.12               # start reading the dice this long after they stop (earlier frames often still rock)
MIN_VOTE_FRAMES = 4             # never report on fewer readings than this, even on a slow camera
MOTION_SPAN = 0.25              # each picture is checked for movement against the one this long before it, so a die
                                # sliding slowly still counts as moving on a fast camera (14 pictures a second shows
                                # too little change from one picture to the next); on a slow camera it's the last one
MOTION_THRESHOLD = 1.5          # how much picture change counts as "moving"
MOTION_PIXELS = 60              # this many changed pixels (in the 480-wide copy) also counts, so small dice landing are noticed
AGREEMENT_NEEDED = 0.7          # share of frames that must agree on the reading
MIN_CLEAN_SHARE = 0.3           # share of frames that must read two dice cleanly when the others are a blur
PREVIEW_WIDTH = 480             # the long side of the little live picture in the tracker
ROI = None                      # [left, top, right, bottom] of the carpet in the picture as read (turned by ROTATE),
                                # for a 1920x1080 picture; None = whole picture
UNCLEAR_DIR = os.path.join(HERE, "unclear_frames")     # a picture of each throw the camera couldn't read, for checking later

FACE_SIZE = 64                  # each die's top face is straightened to this many pixels square
LAYOUT_TOLERANCE = 0.35         # how far pips may sit from the ideal layout (bigger = more forgiving)

MODEL_FILE = os.path.join(HERE, "dice_model.npz")
USE_MODEL = True                # False leaves the model out: it only knows the dice it was trained on
MODEL_NEIGHBOURS = 7            # the model compares a die with this many similar saved dice
MODEL_AGREEMENT = 0.8           # share of those that must agree before the model's answer is used

# Sizes (MIN_DIE_AREA) are for a picture 1920 pixels on its long side and are scaled for smaller ones
# (the long side, so a picture turned on its side by ROTATE keeps the same scale).
REFERENCE_WIDTH = 1920

# If dice_settings.json sits next to this file (made by pressing S in
# dice_camera_test.py), its numbers replace the ones above.
SETTINGS_FILE = os.path.join(HERE, "dice_settings.json")
_SAVED_NAMES = {"hue_low": "HUE_LOW", "hue_high": "HUE_HIGH", "sat_low": "SAT_LOW",
                "val_low": "VAL_LOW", "min_die_area": "MIN_DIE_AREA",
                "white_level": "WHITE_LEVEL", "exposure": "EXPOSURE", "roi": "ROI", "focus": "FOCUS",
                "two_dice_area": "TWO_DICE_AREA", "gain": "GAIN", "exposure_linux": "EXPOSURE_LINUX",
                "rotate": "ROTATE", "white_balance": "WHITE_BALANCE", "felt_hue": "FELT_HUE",
                "nadir": "NADIR", "tilt": "TILT", "use_model": "USE_MODEL"}


def load_settings(path=None):
    """Use the numbers in dice_settings.json (or the file given) in place of the ones above."""
    try:
        with open(path or SETTINGS_FILE) as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return
    for key, name in _SAVED_NAMES.items():
        if key in saved:
            globals()[name] = saved[key]


def apply_settings(values):
    """Use these settings (keys as in dice_settings.json; others are left out) in place of the ones now in use.
    They're this module's own, so there is one set at a time: the web server runs one reader at a time."""
    for key, value in values.items():
        name = _SAVED_NAMES.get(key)
        if name:
            globals()[name] = value


def turn(frame):
    """The picture turned by ROTATE, the way the reader sees it."""
    turns = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
    way = turns.get(int(ROTATE or 0) % 360)
    return frame if way is None else cv2.rotate(frame, way)


def scale_of(frame):
    """The picture's long side over REFERENCE_WIDTH: 1.0 for a 1920x1080 picture, either way up."""
    return max(frame.shape[:2]) / REFERENCE_WIDTH


def current_settings():
    """The settings dice_settings.json can hold, as they are now."""
    return {key: globals()[name] for key, name in _SAVED_NAMES.items()}


load_settings()


# ------------------------------------------------------------ pip layouts
# Where the pips sit on each face, as fractions of the face (0 = one edge, 1 = the other).
PIP_LAYOUTS = {
    1: [(.5, .5)],
    2: [(.25, .25), (.75, .75)],
    3: [(.25, .25), (.5, .5), (.75, .75)],
    4: [(.25, .25), (.75, .25), (.25, .75), (.75, .75)],
    5: [(.25, .25), (.75, .25), (.5, .5), (.25, .75), (.75, .75)],
    6: [(.25, .25), (.25, .5), (.25, .75), (.75, .25), (.75, .5), (.75, .75)],
}


def _centre_and_scale(points):
    """Shift points so their middle is at 0,0 and resize them to a standard spread.
    That way a layout matches no matter where on the face, or how big, the pips are."""
    pts = np.asarray(points, dtype=np.float64)
    pts = pts - pts.mean(axis=0)
    spread = float(np.sqrt((pts ** 2).sum(axis=1).mean()))
    if spread > 0:
        pts = pts / spread
    return pts, spread


def _turned_versions(points):
    """The same layout turned by 90, 180 and 270 degrees (a 2, 3 or 6 can lie either way)."""
    versions = []
    pts = list(points)
    for _ in range(4):
        key = sorted((round(x, 3), round(y, 3)) for x, y in pts)
        if key not in versions:
            versions.append(key)
        pts = [(1 - y, x) for x, y in pts]
    return [_centre_and_scale(v)[0] for v in versions]


_LAYOUT_SHAPES = {n: _turned_versions(points) for n, points in PIP_LAYOUTS.items()}


def layout_fits(centres, any_angle=False):
    """True if these pip positions look like a real die face.
    The face is 0-1 across when any_angle is False; otherwise the positions only need to be in
    units of the die's width, and the die may be turned to any angle."""
    n = len(centres)
    if n == 0 or n > 6:
        return False
    if n == 1:
        return True
    pts, spread = _centre_and_scale(centres)
    if not 0.10 < spread < 0.50:           # pips bunched in a corner or spread past the edges
        return False
    turns = range(0, 90, 6) if any_angle else [0]    # the shapes already cover 90-degree turns
    for degrees in turns:
        a = np.radians(degrees)
        rotated = pts @ np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
        for shape in _LAYOUT_SHAPES[n]:
            distance = np.linalg.norm(rotated[:, None, :] - shape[None, :, :], axis=2)
            worst = max(distance.min(axis=1).max(), distance.min(axis=0).max())
            if worst < LAYOUT_TOLERANCE:
                return True
    return False


# ----------------------------------------------------------------- the model
def face_features(face):
    """Turn a straightened face picture into the numbers the model compares."""
    gray = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY) if face.ndim == 3 else face
    small = cv2.resize(gray, (24, 24), interpolation=cv2.INTER_AREA).astype(np.float32)
    small -= small.mean()
    small /= small.std() + 1e-6
    return small.ravel()


def turned_and_mirrored(face):
    """A face picture in all 8 orientations. Any of them is still the same face."""
    versions = []
    for k in range(4):
        turned = np.rot90(face, k)
        versions.append(np.ascontiguousarray(turned))
        versions.append(np.ascontiguousarray(np.fliplr(turned)))
    return versions


_model = None
MODEL_STATUS = "no model yet"


def load_model(path=None):
    """Load dice_model.npz if it exists. Called automatically when this file is imported."""
    global _model, MODEL_STATUS
    try:
        with np.load(path or MODEL_FILE) as data:
            features = data["features"].astype(np.float32)
            labels = data["labels"].astype(np.int64)
            faces = int(data["faces"]) if "faces" in data.files else len(labels) // 8
    except (OSError, KeyError, ValueError):
        _model = None
        MODEL_STATUS = "no model yet"
        return False
    _model = (features, labels)
    MODEL_STATUS = f"model from {faces} saved dice"
    return True


def model_guess(face):
    """Return (value, agreement) from the trained model, or (None, 0) if there is no model."""
    if _model is None or not USE_MODEL:
        return None, 0.0
    features, labels = _model
    distances = ((features - face_features(face)) ** 2).sum(axis=1)
    k = min(MODEL_NEIGHBOURS, len(distances))
    nearest = np.argpartition(distances, k - 1)[:k]
    value, votes = Counter(labels[nearest].tolist()).most_common(1)[0]
    return int(value), votes / k


load_model()


# --------------------------------------------------------------- reading dice
def _order_corners(box):
    """Put the 4 corners of a rectangle in a consistent order around its middle."""
    middle = box.mean(axis=0)
    angles = np.arctan2(box[:, 1] - middle[1], box[:, 0] - middle[0])
    return box[np.argsort(angles)].astype(np.float32)


def top_face(outline, centre, scale):
    """Just the top face of a die seen at a slant, or None if the camera is taken to look straight down.
    Away from the spot straight under the camera (NADIR) a die shows the side facing the camera as well, and
    its pips would be counted with the top's. The top face sits on the far side of what's seen, and the side
    face fills the gap back towards NADIR, a band that widens with the distance from it (TILT of that
    distance). So the outline moved TILT further out, overlapping the outline itself, leaves the top face."""
    if not TILT or NADIR is None:
        return None
    shift = [TILT * (centre[0] - NADIR[0] * scale), TILT * (centre[1] - NADIR[1] * scale)]
    moved = cv2.warpAffine(outline, np.float32([[1, 0, shift[0]], [0, 1, shift[1]]]), outline.shape[::-1])
    top = cv2.bitwise_and(outline, moved)
    if cv2.countNonZero(top) < 0.3 * cv2.countNonZero(outline):
        return None
    return top


def _top_outline(contour, scale):
    """The outline of a blob's top face (see top_face), or the blob itself when there's no slant to trim."""
    hull = cv2.convexHull(contour)
    hx, hy, hw, hh = cv2.boundingRect(hull)
    outline = np.zeros((hh, hw), np.uint8)
    cv2.drawContours(outline, [(hull - np.array([hx, hy])).astype(np.int32)], -1, 255, cv2.FILLED)
    top = top_face(outline, (hx + hw / 2, hy + hh / 2), scale)
    if top is None:
        return contour
    found, _ = cv2.findContours(top, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return max(found, key=cv2.contourArea) + np.array([hx, hy])


def _find_pips(frame, outline, hx, hy):
    """Pip centres (in units of the die's width) inside outline, a mask over the picture from (hx, hy)."""
    die_area = max(cv2.countNonZero(outline), 1)
    hh, hw = outline.shape
    outline = cv2.dilate(outline, np.ones((5, 5), np.uint8))   # pips on the very edge still count
    gray = cv2.cvtColor(frame[hy:hy + hh, hx:hx + hw], cv2.COLOR_BGR2GRAY)
    # A pale die body can pass WHITE_LEVEL and fuse with the pips, so also demand near-peak brightness
    level = max(WHITE_LEVEL, int(np.percentile(gray[outline > 0], 99)) - 20)
    pips_only = cv2.bitwise_and(cv2.inRange(gray, level, 255), outline)
    pips_only = cv2.erode(pips_only, np.ones((3, 3), np.uint8))     # pulls apart pips that touch
    count, _, stats, centroids = cv2.connectedComponentsWithStats(pips_only)

    side = np.sqrt(die_area)
    centres = []
    for i in range(1, count):                               # 0 is the background
        bw, bh = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
        blob_area = stats[i, cv2.CC_STAT_AREA]
        if blob_area < 0.004 * die_area:
            continue                                        # too small: a speck
        if blob_area > 0.10 * die_area:
            continue                                        # too big: glare or merged pips
        if not 0.5 < bw / bh < 2.0:
            continue                                        # too long or thin (a pip seen on a side face)
        if blob_area / (bw * bh) > 0.92 and blob_area > 0.03 * die_area:
            continue                                        # big and boxy, like glare
        centres.append((centroids[i][0] / side, centroids[i][1] / side))
    return centres, side


def _farthest_face(centres, side, hx, hy, scale):
    """The pips of a die seen at a slant that make a whole face, taking the ones farthest from NADIR first:
    the side face showing is the one towards it. Returns how many, or None if no two or more make a face."""
    if NADIR is None or len(centres) < 2:
        return None
    nadir = np.array(NADIR, dtype=np.float64) * scale
    spots = np.array([(hx + x * side, hy + y * side) for x, y in centres])
    away = spots.mean(axis=0) - nadir
    order = np.argsort(-(spots @ (away / max(np.linalg.norm(away), 1e-6))))
    for n in range(len(centres), 1, -1):
        if layout_fits([centres[i] for i in order[:n]], any_angle=True):
            return n
    return None


def read_die(frame, contour):
    """Straighten one die's face, count its pips and check the layout."""
    # Pips are found on the die in the original picture rather than in the straightened square: a
    # side face squashed into the square puts its pips in the way, but there they show up as flat
    # ellipses and are rejected below. The hull is used because white pips leave dents in the green mask.
    hull = cv2.convexHull(contour)
    hx, hy, hw, hh = cv2.boundingRect(hull)
    outline = np.zeros((hh, hw), np.uint8)
    cv2.drawContours(outline, [(hull - np.array([hx, hy])).astype(np.int32)], -1, 255, cv2.FILLED)
    scale = scale_of(frame)
    top = top_face(outline, (hx + hw / 2, hy + hh / 2), scale)
    face_outline = contour
    if top is not None:
        found, _ = cv2.findContours(top, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        face_outline = max(found, key=cv2.contourArea) + np.array([hx, hy])

    size = FACE_SIZE
    corners = _order_corners(cv2.boxPoints(cv2.minAreaRect(face_outline)))
    square = np.float32([[0, 0], [size - 1, 0], [size - 1, size - 1], [0, size - 1]])
    face = cv2.warpPerspective(frame, cv2.getPerspectiveTransform(corners, square), (size, size),
                               flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)

    # Seen at a slant (TILT set): the most pips, farthest from the camera's spot first, that make a face; failing
    # that, the pips on the top face as trimmed. The trim alone is rough: where the side face is in shadow it
    # cuts into the top, and the two corner pips left of a 4, 5 or 6 make a 2.
    centres, side = _find_pips(frame, outline, hx, hy)
    pips, value = len(centres), None
    if layout_fits(centres, any_angle=True):
        value = pips
    elif top is not None:
        value = _farthest_face(centres, side, hx, hy, scale)
        if value is None:
            centres, _ = _find_pips(frame, top, hx, hy)
            if layout_fits(centres, any_angle=True):
                value = len(centres)
        pips = value or pips
    if value is not None:
        method = "pips"
    else:
        guess, agreement = model_guess(face)
        if guess is not None and agreement >= MODEL_AGREEMENT:
            value, method = guess, "model"
        else:
            value, method = None, "unclear"
    return {"contour": contour, "pips": pips, "value": value, "method": method, "face": face}


def split_touching(contour, shape, two_dice_area):
    """Cut a blob that is clearly two dice (they sit close, or the mask bridges the gap) into one contour per die.
    A die seen at a slant is long too, with its side face, so a long blob is only two dice if its top face (see
    top_face) is long as well. Both are asked because the trim is rough: where the side face is in shadow it
    can cut a square die down to a strip."""
    def long(outline):
        (_, (w, h), _) = cv2.minAreaRect(outline)
        return max(w, h) / max(min(w, h), 1) >= SPLIT_ASPECT

    if cv2.contourArea(contour) < two_dice_area and not (
            long(contour) and long(_top_outline(contour, max(shape) / REFERENCE_WIDTH))):
        return [contour]
    parts = 2
    blob = np.zeros(shape, np.uint8)
    cv2.drawContours(blob, [contour], -1, 255, cv2.FILLED)
    ys, xs = np.nonzero(blob)
    points = np.column_stack([xs, ys]).astype(np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
    _, labels, _ = cv2.kmeans(points, parts, None, criteria, 5, cv2.KMEANS_PP_CENTERS)
    pieces = []
    for k in range(parts):
        piece = np.zeros(shape, np.uint8)
        sel = labels.ravel() == k
        piece[ys[sel], xs[sel]] = 255
        pieces.append(cv2.convexHull(np.column_stack([xs[sel], ys[sel]]).astype(np.int32)))   # hull keeps stray bits at the edge
    return pieces or [contour]


def die_shaped(contour, two_dice_area):
    """True if a blob is the size and shape of one die seen from above: square-ish, solid, not too big.
    A die in a wall's shadow is only green around its edge, so "solid" has to allow a hollow middle."""
    area = cv2.contourArea(contour)
    (_, (w, h), _) = cv2.minAreaRect(contour)
    solidity = area / max(cv2.contourArea(cv2.convexHull(contour)), 1)
    return area < two_dice_area and max(w, h) / max(min(w, h), 1) < SPLIT_ASPECT and solidity > 0.6


def green_mask(area, scale=None):
    """White where the picture is die-green, after cleaning up specks and joining see-through dice.
    scale is the whole picture's width over REFERENCE_WIDTH (area may be just the carpet)."""
    scale = scale_of(area) if scale is None else scale
    close_size = max(3, int(round(15 * scale)) | 1)     # odd number, shrinks for small pictures
    hsv = cv2.cvtColor(area, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, np.array([HUE_LOW, SAT_LOW, VAL_LOW]), np.array([HUE_HIGH, 255, 255]))
    # "Open" removes speckles, "close" joins the pieces of a see-through die into one blob
    green = cv2.morphologyEx(green, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return cv2.morphologyEx(green, cv2.MORPH_CLOSE, np.ones((close_size, close_size), np.uint8))


def pale_mask(area, scale, min_area, two_area):
    """White over each spot in area that could be a die washed out by glare: not felt, die-sized, and ringed by
    felt (so the walls, the laptop and anything else at the edge of the carpet are left out). Empty if FELT_HUE
    isn't set."""
    out = np.zeros(area.shape[:2], np.uint8)
    if FELT_HUE is None:
        return out
    hsv = cv2.cvtColor(area, cv2.COLOR_BGR2HSV)
    off = np.abs(hsv[..., 0].astype(np.int16) - int(FELT_HUE))
    off = np.minimum(off, 180 - off)                    # red sits at both ends of the hue circle
    felt = ((off <= FELT_HUE_RANGE) & (hsv[..., 1] >= FELT_SAT_LOW)
            & (hsv[..., 2] >= FELT_VAL_LOW)).astype(np.uint8) * 255
    spots = cv2.morphologyEx(cv2.bitwise_not(felt), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    ring_size = max(3, int(round(9 * scale)) | 1)
    contours, _ = cv2.findContours(spots, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in contours:
        hull = cv2.convexHull(c)                        # the white pips and the glare leave gaps in the spot
        if not min_area <= cv2.contourArea(hull) < two_area:
            continue
        x, y, w, h = cv2.boundingRect(hull)
        pad = ring_size
        x0, y0 = max(x - pad, 0), max(y - pad, 0)
        x1, y1 = min(x + w + pad, area.shape[1]), min(y + h + pad, area.shape[0])
        if (x0, y0, x1, y1) != (x - pad, y - pad, x + w + pad, y + h + pad):
            continue                                    # touches the edge of the search area
        blob = np.zeros((y1 - y0, x1 - x0), np.uint8)
        cv2.drawContours(blob, [hull - np.array([x0, y0])], -1, 255, cv2.FILLED)
        ring = cv2.dilate(blob, np.ones((ring_size, ring_size), np.uint8)) & ~blob
        if cv2.countNonZero(felt[y0:y1, x0:x1] & ring) >= 0.8 * cv2.countNonZero(ring):
            out[y0:y1, x0:x1] |= blob
    return out


def roi_of(frame):
    """ROI in this picture's pixels, [left, top, right, bottom], or None for the whole picture."""
    if not ROI:
        return None
    scale = scale_of(frame)
    return [int(v * scale) for v in ROI]


def _search(frame):
    """What find_dice looks in: the mask of die-coloured spots on the carpet (ROI), where that sits in the
    picture, and the die sizes for this picture's scale."""
    scale = scale_of(frame)                             # 1.0 for a 1920x1080 picture
    min_area = MIN_DIE_AREA * scale * scale
    two_area = TWO_DICE_AREA * scale * scale
    x0, y0, area = 0, 0, frame
    roi = roi_of(frame)
    if roi:
        x0, y0, x1, y1 = roi
        area = frame[y0:y1, x0:x1]
    mask = green_mask(area, scale) | pale_mask(area, scale, min_area, two_area)
    return mask, (x0, y0), min_area, two_area


def dice_mask(frame):
    """White where find_dice could find a die (green, or pale and ringed by felt), the size of the whole picture
    and black outside ROI: for tuning, to see what the reader takes for dice."""
    mask, (x0, y0), _, _ = _search(frame)
    full = np.zeros(frame.shape[:2], np.uint8)
    full[y0:y0 + mask.shape[0], x0:x0 + mask.shape[1]] = mask
    return full


def find_dice(frame):
    """Return one reading (see read_die) per die in the frame, left to right."""
    # Only the carpet is searched; contours are shifted back so they match the full picture
    green, (x0, y0), min_area, two_area = _search(frame)

    contours, _ = cv2.findContours(green, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    dice = []
    for c in contours:
        c = c + np.array([x0, y0], dtype=c.dtype)
        if cv2.contourArea(c) >= min_area:
            dice.extend(read_die(frame, part) for part in split_touching(c, frame.shape[:2], two_area))
    # A pipless blob is usually carpet that passed the green test, but glare can swallow every pip on a
    # real die. Dropping that die turns the throw into "I see 1 die", so die-shaped blobs are kept for the model.
    dice = [d for d in dice if d["pips"] > 0 or die_shaped(d["contour"], two_area)]
    dice.sort(key=lambda d: cv2.boundingRect(d["contour"])[0])
    return dice


def die_centres(dice):
    centres = []
    for die in dice:
        x, y, w, h = cv2.boundingRect(die["contour"])
        centres.append((x + w // 2, y + h // 2))
    return centres


def encode_png(image):
    ok, png = cv2.imencode(".png", image)
    return png.tobytes() if ok else b""


# ----------------------------------------------------------- the live camera
WINDOWS = sys.platform.startswith("win")
LINUX = sys.platform.startswith("linux")


def linux_exposure():
    """EXPOSURE_LINUX, or EXPOSURE turned into Linux's units. On Windows EXPOSURE is a power of two in
    seconds (-3 = 1/8 s); Linux cameras take tenths of a millisecond (1/8 s = 1250)."""
    if EXPOSURE_LINUX is not None:
        return EXPOSURE_LINUX
    if EXPOSURE is None:
        return None
    return max(1, round(2 ** EXPOSURE * 10000)) if EXPOSURE < 0 else EXPOSURE


def apply_focus(cap):
    if FOCUS is not None:
        cap.set(cv2.CAP_PROP_FOCUS, FOCUS)


def apply_white_balance(cap):
    if WHITE_BALANCE is not None:
        cap.set(cv2.CAP_PROP_AUTO_WB, 0)
        cap.set(cv2.CAP_PROP_WB_TEMPERATURE, WHITE_BALANCE)


def apply_exposure(cap):
    exposure = linux_exposure() if LINUX else EXPOSURE
    if exposure is not None:
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1 if LINUX else 0.25)     # manual: 0.25 on Windows cameras, 1 on Linux
        cap.set(cv2.CAP_PROP_EXPOSURE, exposure)
        if GAIN is not None:
            cap.set(cv2.CAP_PROP_GAIN, GAIN)


def open_camera(source):
    """Open a camera (a number, or a device path like /dev/video0 on Linux) with the settings above. None if it won't open."""
    backend = cv2.CAP_DSHOW if WINDOWS else cv2.CAP_V4L2 if LINUX else cv2.CAP_ANY
    cap = cv2.VideoCapture(source, backend)
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)
    apply_focus(cap)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)        # always hand us the newest picture, not a queued one
    apply_exposure(cap)
    apply_white_balance(cap)
    return cap


class DiceCamera:
    """
    Watches the camera in a background thread.

    Things the tracker can use:
      camera.events   a queue of:
                        ("throw",   {"dice": (left, right), "crops": [png, png]})
                        ("unclear", {"message": text, "crops": [png, ...]})
                        ("error",   text)
      camera.status   a short text such as "moving" or "settled (2 dice)"
      camera.resolution   (width, height) the camera is really giving us
      camera.fps      frames per second the camera is really giving us
      camera.get_preview_png()   the latest small picture, for showing on screen
      camera.get_preview_jpeg()  the same picture as JPEG bytes, for a web page
      camera.view     "normal", or "mask" to show the green the dice are found by (for tuning)
      camera.seen     the dice the reader found last (see read_die), as boxes for drawing: seen_boxes()
      camera.frame    the newest picture, as read (turned); mask_jpeg() shows it as dice_mask sees it
      camera.reapply()  send changed FOCUS / EXPOSURE / GAIN to the camera
    "crops" are the straightened top faces, left die first, as PNG bytes.
    capture can be anything with read() and release() in place of a real camera (for tests, or the pictures
    a web page sends). turn_pictures=False reads them as they come, for pictures already turned by whoever
    sent them (the web page turns its picture by its own Turn button, so what you see is what's read).
    """

    def __init__(self, camera_index=0, capture=None, turn_pictures=True):
        self.camera_index = camera_index
        self.capture = capture
        self.turn_pictures = turn_pictures
        self.view = "normal"
        self._reapply = False
        self._preview_small = None
        self.events = queue.Queue()
        self.status = "starting"
        self.error = ""
        self.resolution = None
        self.fps = None
        self._stop = threading.Event()
        self._thread = None
        self._cap = None
        self._lock = threading.Lock()
        self._preview = None
        self._last_report = None      # die centres of the last thing we reported
        self._first_look = True       # the first time the picture settles is what was there before anyone threw
        self.seen = []                # the dice found in the last picture read, for drawing over the live picture
        self.frame = None

    @property
    def model_status(self):
        return MODEL_STATUS

    # ---- starting and stopping
    def start(self):
        """Open the camera and start watching. Returns False (with .error set) on failure."""
        cap = self.capture or open_camera(self.camera_index)
        if cap is None:
            if WINDOWS:
                self.error = (f"Could not open camera {self.camera_index}. "
                              "Close other apps using it, or start the tracker with a different "
                              "camera number, like:  python dice_tracker_v2.py 1")
            else:
                self.error = (f"Could not open camera {self.camera_index}. Check it's plugged in "
                              "(ls /dev/video*) and nothing else is using it.")
            return False
        self._cap = cap
        self._first_look = True
        self._last_report = None
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def reapply(self):
        self._reapply = True

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self.status = "stopped"

    # ---- the background loop
    def _run(self):
        cap = self._cap
        history = deque()               # (time, small picture) back to the newest one at least MOTION_SPAN old
        still_since = None              # when the picture last stopped moving
        armed = True                    # True = ready to report the next settled throw
        baseline = None                 # small picture from the last report, to notice dice appearing quietly
        recent = deque(maxlen=30)       # (reading, dice) for the still frames read so far
        dice = []
        frame_number = 0
        fps_start, fps_frames = time.perf_counter(), 0
        started = fps_start

        while not self._stop.is_set():
            if self._reapply and self.capture is None:
                self._reapply = False
                apply_focus(cap)
                apply_exposure(cap)
                apply_white_balance(cap)
            ok, frame = cap.read()
            if not ok:
                self.error = "Lost the camera feed."
                self.events.put(("error", self.error))
                break
            if self.turn_pictures:
                frame = turn(frame)
            self.frame = frame
            frame_number += 1
            now = time.perf_counter()
            if self.resolution is None:
                self.resolution = (frame.shape[1], frame.shape[0])
            fps_frames += 1
            if now - fps_start >= 2:
                self.fps = fps_frames / (now - fps_start)
                fps_start, fps_frames = now, 0

            # Motion check on a small, blurred copy of the picture
            size = (480, 270) if frame.shape[1] >= frame.shape[0] else (270, 480)
            small = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
            small = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
            history.append((now, small))
            while len(history) > 2 and now - history[1][0] >= MOTION_SPAN:
                history.popleft()
            if len(history) < 2:
                moving = True
            else:
                diff = cv2.absdiff(small, history[0][1])
                moving = (float(np.mean(diff)) > MOTION_THRESHOLD
                          or cv2.countNonZero(cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)[1]) > MOTION_PIXELS)
            moving = moving or now - started < WARMUP_SECONDS

            if moving:
                still_since = None
                armed = True
                recent.clear()
                dice = []
                self.status = "moving"
            else:
                if still_since is None:
                    still_since = now
                if not armed and baseline is not None and cv2.countNonZero(
                        cv2.threshold(cv2.absdiff(small, baseline), 25, 255, cv2.THRESH_BINARY)[1]) > MOTION_PIXELS:
                    armed = True            # the scene changed since the last report without enough motion to notice
                    still_since = now
                    recent.clear()
                still_for = now - still_since
                if armed and still_for >= READ_AFTER:
                    dice = find_dice(frame)
                    recent.append((tuple(d["value"] for d in dice), dice))
                    if still_for >= STILL_SECONDS and len(recent) >= MIN_VOTE_FRAMES:
                        armed = False
                        baseline = small
                        self._report(recent, frame)
                self.status = "watching..." if armed else f"settled ({len(dice)} dice)"
            self.seen = dice

            if frame_number % 3 == 0:
                self._make_preview(frame, dice)

    def _report(self, recent, frame):
        """The dice have stopped. Vote across the recent frames and report the result."""
        votes = Counter(reading for reading, _ in recent)
        reading, count = votes.most_common(1)[0]
        agreement = count / len(recent)
        # Touching dice can flicker between "two dice read" and "one unreadable blob" from frame to frame.
        # If enough frames read two dice cleanly and those frames agree, trust them over the flicker.
        clean = Counter(r for r, _ in recent if len(r) == 2 and None not in r)
        if clean and not (len(reading) == 2 and None not in reading):
            best, best_count = clean.most_common(1)[0]
            if best_count >= MIN_CLEAN_SHARE * len(recent) and best_count / sum(clean.values()) >= AGREEMENT_NEEDED:
                reading, agreement = best, 1.0
        # Pictures and positions from the latest frame that gave the winning reading
        dice = next(d for r, d in reversed(recent) if r == reading)
        centres = die_centres(dice)
        crops = [encode_png(d["face"]) for d in dice]

        if len(reading) == 0:
            self._last_report = None    # empty carpet: the next throw is always new
            self._first_look = False
            return

        # Dice already sitting on the table when the camera starts are left over from before: not a throw.
        # Remember where they are so they're passed by like a throw already reported, until someone throws.
        if self._first_look:
            self._first_look = False
            self._last_report = centres
            return

        # Dice still where they were last reported are the same throw, seen again after a hand or shadow
        # passed over them. Hard-to-read dice often read differently the second time, and reporting that
        # would log an extra roll, which F2 would then fix instead of the misread one.
        if self._last_report is not None and all(
                any(abs(a[0] - b[0]) < 20 and abs(a[1] - b[1]) < 20 for b in self._last_report) for a in centres):
            return
        self._last_report = centres

        if len(reading) == 2 and None not in reading and agreement >= AGREEMENT_NEEDED:
            self.events.put(("throw", {"dice": reading, "crops": crops}))
            return
        # PNG, because JPG's colour smearing makes carpet look green and hides what really went wrong
        os.makedirs(UNCLEAR_DIR, exist_ok=True)
        cv2.imwrite(os.path.join(UNCLEAR_DIR, datetime.now().strftime("%Y%m%d-%H%M%S.png")), frame)
        if len(reading) != 2:
            self.events.put(("unclear", {
                "message": f"Dice stopped but I see {len(reading)} dice - type the throw in",
                "crops": []}))
        else:
            self.events.put(("unclear", {
                "message": "Dice stopped but I couldn't read them - type the throw in "
                           "(left die first, as in the camera picture)",
                "crops": crops}))

    # ---- the little live picture
    def _make_preview(self, frame, dice):
        scale = PREVIEW_WIDTH / max(frame.shape[:2])
        shown = cv2.cvtColor(green_mask(frame), cv2.COLOR_GRAY2BGR) if self.view == "mask" else frame
        small = cv2.resize(shown, (int(frame.shape[1] * scale), int(frame.shape[0] * scale)), interpolation=cv2.INTER_AREA)
        for die in dice:
            x, y, w, h = cv2.boundingRect(die["contour"])
            x, y, w, h = int(x * scale), int(y * scale), int(w * scale), int(h * scale)
            readable = die["value"] is not None
            colour = (0, 255, 0) if readable else (0, 215, 255)       # green = read, yellow = "?"
            label = str(die["value"]) if readable else "?"
            cv2.rectangle(small, (x, y), (x + w, y + h), colour, 1)
            cv2.putText(small, label, (x, max(y - 3, 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1)
        ok, png = cv2.imencode(".png", small)
        if ok:
            data = base64.b64encode(png.tobytes()).decode("ascii")
            with self._lock:
                self._preview = data
                self._preview_small = small

    def seen_boxes(self):
        """The dice found last, as [{"box": [x, y, w, h], "value": 1-6 or None, "method": ...}] in the picture's
        pixels, for a page to draw over its own picture."""
        return [{"box": [int(v) for v in cv2.boundingRect(d["contour"])], "value": d["value"], "method": d["method"]}
                for d in self.seen]

    def mask_jpeg(self, width=PREVIEW_WIDTH, quality=80):
        """The newest picture as dice_mask sees it (white = could be a die, black outside ROI), small, as JPEG
        bytes; None before the first picture."""
        frame = self.frame
        if frame is None:
            return None
        mask = dice_mask(frame)
        scale = width / max(frame.shape[:2])
        small = cv2.resize(mask, (int(frame.shape[1] * scale), int(frame.shape[0] * scale)), interpolation=cv2.INTER_AREA)
        ok, jpeg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return jpeg.tobytes() if ok else None

    def get_preview_png(self):
        """Latest preview as base64 PNG text (what Tkinter's PhotoImage wants), or None."""
        with self._lock:
            return self._preview

    def get_preview_jpeg(self, quality=80):
        """Latest preview as JPEG bytes, or None."""
        with self._lock:
            small = self._preview_small
        if small is None:
            return None
        ok, jpeg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return jpeg.tobytes() if ok else None
