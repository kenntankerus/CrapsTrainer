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

EXPOSURE = -2                   # camera brightness; None lets the camera choose
FOCUS = None                    # lens focus with autofocus off (Brio: 0-255, lower = farther away); None keeps the camera's own
FRAME_WIDTH, FRAME_HEIGHT = 1920, 1080

STILL_SECONDS = 0.35            # how long the dice must sit still before the throw is reported
READ_AFTER = 0.12               # start reading the dice this long after they stop (earlier frames often still rock)
MIN_VOTE_FRAMES = 4             # never report on fewer readings than this, even on a slow camera
MOTION_THRESHOLD = 1.5          # how much picture change counts as "moving"
MOTION_PIXELS = 60              # this many changed pixels (in the 480-wide copy) also counts, so small dice landing are noticed
AGREEMENT_NEEDED = 0.7          # share of frames that must agree on the reading
MIN_CLEAN_SHARE = 0.3           # share of frames that must read two dice cleanly when the others are a blur
PREVIEW_WIDTH = 480             # width of the little live picture in the tracker
ROI = None                      # [left, top, right, bottom] of the carpet in a 1920-wide picture; None = whole picture
UNCLEAR_DIR = os.path.join(HERE, "unclear_frames")     # a picture of each throw the camera couldn't read, for checking later

FACE_SIZE = 64                  # each die's top face is straightened to this many pixels square
LAYOUT_TOLERANCE = 0.35         # how far pips may sit from the ideal layout (bigger = more forgiving)

MODEL_FILE = os.path.join(HERE, "dice_model.npz")
MODEL_NEIGHBOURS = 7            # the model compares a die with this many similar saved dice
MODEL_AGREEMENT = 0.8           # share of those that must agree before the model's answer is used

# Sizes (MIN_DIE_AREA) are for a 1920-pixel-wide picture and are scaled for smaller ones.
REFERENCE_WIDTH = 1920

# If dice_settings.json sits next to this file (made by pressing S in
# dice_camera_test.py), its numbers replace the ones above.
SETTINGS_FILE = os.path.join(HERE, "dice_settings.json")
_SAVED_NAMES = {"hue_low": "HUE_LOW", "hue_high": "HUE_HIGH", "sat_low": "SAT_LOW",
                "val_low": "VAL_LOW", "min_die_area": "MIN_DIE_AREA",
                "white_level": "WHITE_LEVEL", "exposure": "EXPOSURE", "roi": "ROI", "focus": "FOCUS",
                "two_dice_area": "TWO_DICE_AREA"}


def _load_saved_settings():
    try:
        with open(SETTINGS_FILE) as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return
    for key, name in _SAVED_NAMES.items():
        if key in saved:
            globals()[name] = saved[key]


_load_saved_settings()


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
    if _model is None:
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


def read_die(frame, contour):
    """Straighten one die's face, count its pips and check the layout."""
    size = FACE_SIZE
    corners = _order_corners(cv2.boxPoints(cv2.minAreaRect(contour)))
    square = np.float32([[0, 0], [size - 1, 0], [size - 1, size - 1], [0, size - 1]])
    face = cv2.warpPerspective(frame, cv2.getPerspectiveTransform(corners, square), (size, size),
                               flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)

    # Pips are found on the die in the original picture rather than in the straightened square: a
    # side face squashed into the square puts its pips in the way, but there they show up as flat
    # ellipses and are rejected below. The hull is used because white pips leave dents in the green mask.
    hull = cv2.convexHull(contour)
    hx, hy, hw, hh = cv2.boundingRect(hull)
    outline = np.zeros((hh, hw), np.uint8)
    cv2.drawContours(outline, [(hull - np.array([hx, hy])).astype(np.int32)], -1, 255, cv2.FILLED)
    die_area = max(cv2.countNonZero(outline), 1)
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

    pips = len(centres)
    if layout_fits(centres, any_angle=True):
        value, method = pips, "pips"
    else:
        guess, agreement = model_guess(face)
        if guess is not None and agreement >= MODEL_AGREEMENT:
            value, method = guess, "model"
        else:
            value, method = None, "unclear"
    return {"contour": contour, "pips": pips, "value": value, "method": method, "face": face}


def split_touching(contour, shape, two_dice_area):
    """Cut a blob that is clearly two dice (they sit close, or the mask bridges the gap) into one contour per die."""
    (_, (w, h), _) = cv2.minAreaRect(contour)
    if max(w, h) / max(min(w, h), 1) < SPLIT_ASPECT and cv2.contourArea(contour) < two_dice_area:
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


def find_dice(frame):
    """Return one reading (see read_die) per die in the frame, left to right."""
    scale = frame.shape[1] / REFERENCE_WIDTH            # 1.0 for a 1920-wide picture
    min_area = MIN_DIE_AREA * scale * scale
    close_size = max(3, int(round(15 * scale)) | 1)     # odd number, shrinks for small pictures

    # Only the carpet is searched; contours are shifted back so they match the full picture
    x0, y0 = 0, 0
    area = frame
    if ROI:
        x0, y0, x1, y1 = (int(v * scale) for v in ROI)
        area = frame[y0:y1, x0:x1]
    hsv = cv2.cvtColor(area, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, np.array([HUE_LOW, SAT_LOW, VAL_LOW]), np.array([HUE_HIGH, 255, 255]))
    # "Open" removes speckles, "close" joins the pieces of a see-through die into one blob
    green = cv2.morphologyEx(green, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    green = cv2.morphologyEx(green, cv2.MORPH_CLOSE, np.ones((close_size, close_size), np.uint8))

    contours, _ = cv2.findContours(green, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    dice = []
    for c in contours:
        c = c + np.array([x0, y0], dtype=c.dtype)
        if cv2.contourArea(c) >= min_area:
            dice.extend(read_die(frame, part) for part in split_touching(c, frame.shape[:2], TWO_DICE_AREA * scale * scale))
    # A real die always shows at least one pip; a pipless blob is a patch of carpet that passed the green test
    dice = [d for d in dice if d["pips"] > 0]
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
    "crops" are the straightened top faces, left die first, as PNG bytes.
    """

    def __init__(self, camera_index=0):
        self.camera_index = camera_index
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
        self._last_report = None      # (reading, centres) of the last thing we reported

    @property
    def model_status(self):
        return MODEL_STATUS

    # ---- starting and stopping
    def start(self):
        """Open the camera and start watching. Returns False (with .error set) on failure."""
        cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            self.error = (f"Could not open camera {self.camera_index}. "
                          "Close other apps using it, or start the tracker with a different "
                          "camera number, like:  python dice_tracker_v2.py 1")
            return False
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
        cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)
        if FOCUS is not None:
            cap.set(cv2.CAP_PROP_FOCUS, FOCUS)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)        # always hand us the newest picture, not a queued one
        if EXPOSURE is not None:
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)     # 0.25 = manual on Windows cameras
            cap.set(cv2.CAP_PROP_EXPOSURE, EXPOSURE)
        self._cap = cap
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

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
        previous = None
        still_since = None              # when the picture last stopped moving
        armed = True                    # True = ready to report the next settled throw
        baseline = None                 # small picture from the last report, to notice dice appearing quietly
        recent = deque(maxlen=30)       # (reading, dice) for the still frames read so far
        dice = []
        frame_number = 0
        fps_start, fps_frames = time.perf_counter(), 0

        while not self._stop.is_set():
            ok, frame = cap.read()
            if not ok:
                self.error = "Lost the camera feed."
                self.events.put(("error", self.error))
                break
            frame_number += 1
            now = time.perf_counter()
            if self.resolution is None:
                self.resolution = (frame.shape[1], frame.shape[0])
            fps_frames += 1
            if now - fps_start >= 2:
                self.fps = fps_frames / (now - fps_start)
                fps_start, fps_frames = now, 0

            # Motion check on a small, blurred copy of the picture
            small = cv2.resize(frame, (480, 270), interpolation=cv2.INTER_AREA)
            small = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
            if previous is None:
                moving = True
            else:
                diff = cv2.absdiff(small, previous)
                moving = (float(np.mean(diff)) > MOTION_THRESHOLD
                          or cv2.countNonZero(cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)[1]) > MOTION_PIXELS)
            previous = small

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
            return

        # The same dice sitting in the same spot (say, after a hand passed over them)
        if self._last_report is not None and self._last_report[0] == reading:
            old = self._last_report[1]
            if len(old) == len(centres) and all(
                    abs(a[0] - b[0]) < 12 and abs(a[1] - b[1]) < 12 for a, b in zip(old, centres)):
                return
        self._last_report = (reading, centres)

        if len(reading) == 2 and None not in reading and agreement >= AGREEMENT_NEEDED:
            self.events.put(("throw", {"dice": reading, "crops": crops}))
            return
        if len(reading) >= 2:       # fewer is usually a hand picking the dice up, not a missed throw
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
        scale = PREVIEW_WIDTH / frame.shape[1]
        small = cv2.resize(frame, (PREVIEW_WIDTH, int(frame.shape[0] * scale)), interpolation=cv2.INTER_AREA)
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

    def get_preview_png(self):
        """Latest preview as base64 PNG text (what Tkinter's PhotoImage wants), or None."""
        with self._lock:
            return self._preview
