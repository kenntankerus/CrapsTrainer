"""
Setting up a camera for the dice reader, from two pictures the page takes: the table empty, then with two dice
on it. Measured rather than asked, so nobody has to know what a hue is.

The empty table gives the felt: its colour (felt_hue, felt_bgr) and brightness, and where it is, which becomes
the search area (roi), a little past the felt so a die against a wall still fits. Then the reader looks at the
empty table with those settings: anything it takes for a die there (a lit laptop screen, a patch of glare) is
trouble, so the search area's edge is moved in past it, whichever edge loses the least felt.

The two dice give their colour (hue_low, hue_high: they wrap round through red for red dice), their size
(min_die_area, two_dice_area) and how bright the pips are against the dice (white_level). Then the reader must
read both with the new settings, and the empty table is checked again with them.

The pictures come turned as the page shows them, full size.
"""

import base64
import threading
from contextlib import contextmanager

import cv2
import numpy as np

import dice_vision as dv

_lock = threading.Lock()
WALL_MARGIN = 40              # pixels (of a 1920 picture) the search area reaches past the felt, for dice at a wall
EDGE_TRIES = 12               # most edges moved in to leave out what looks like a die on an empty table


class SetupError(Exception):
    """Why a picture couldn't be measured, in words for the page."""


def decode(picture):
    """A picture from the page (a data: URL or plain base64 JPEG) as an OpenCV picture."""
    if not isinstance(picture, str) or not picture:
        raise SetupError("No picture came.")
    data = picture.split(",", 1)[1] if picture.startswith("data:") else picture
    try:
        frame = cv2.imdecode(np.frombuffer(base64.b64decode(data), np.uint8), cv2.IMREAD_COLOR)
    except (ValueError, TypeError):
        frame = None
    if frame is None:
        raise SetupError("That isn't a picture.")
    return frame


@contextmanager
def using(values):
    """The reader with these settings for a moment, then as it was. The server runs one reader at a time and it
    isn't running during setup, so nothing else is reading with them meanwhile."""
    with _lock:
        before = dv.current_settings()
        dv.apply_settings(values)
        try:
            yield
        finally:
            dv.apply_settings(before)


def felt_target(felt_bgr, level):
    """The felt's colour as the reader should see it: as measured, at the brightness it was tuned on."""
    bgr = np.array(felt_bgr, float)
    return bgr * (float(level) / max(bgr.max(), 1.0))


def even_gains(frame, felt_bgr, level, roi=None):
    """How much to scale each colour (blue, green, red) so this picture's felt comes out as felt_target, or None when
    the felt can't be told (too little of it in the picture). The felt is found by its hue and colour only, not its
    brightness, which is what's off."""
    small, scale = _small(frame, 240)
    if roi:
        k = max(frame.shape[:2]) / 1920 * scale
        x0, y0, x1, y1 = (int(v * k) for v in roi)
        small = small[y0:y1, x0:x1]
    if small.size == 0:
        return None
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    hue, sat, _ = (int(v) for v in cv2.cvtColor(np.uint8([[felt_bgr]]), cv2.COLOR_BGR2HSV)[0, 0])
    felt = (_hue_distance(hsv[..., 0], hue) <= 12) & (hsv[..., 1] >= 0.4 * sat) & (hsv[..., 2] >= 8)
    if felt.mean() < 0.2:
        return None
    measured = np.median(small[felt], axis=0).astype(float)
    return np.clip(felt_target(felt_bgr, level) / np.maximum(measured, 1.0), 0.33, 3.0)


def gain_table(gains):
    """A lookup table that scales each colour by its gain, for cv2.LUT."""
    ramp = np.arange(256, dtype=float)
    return np.clip(np.stack([ramp * g for g in gains], axis=-1), 0, 255).astype(np.uint8).reshape(1, 256, 3)


def even(frame, settings):
    """The picture with its brightness and colour evened out to the felt measured at setup (felt_bgr, felt_level),
    as the reader sees a camera's pictures; unchanged when that's near enough already, or not measured yet."""
    if not settings.get("felt_bgr"):
        return frame
    gains = even_gains(frame, settings["felt_bgr"], settings.get("felt_level") or 88, settings.get("roi"))
    if gains is None or np.all(np.abs(gains - 1) < 0.06):
        return frame
    return cv2.LUT(frame, gain_table(gains))


def _small(frame, width=480):
    scale = width / max(frame.shape[:2])
    return cv2.resize(frame, (int(frame.shape[1] * scale), int(frame.shape[0] * scale)), interpolation=cv2.INTER_AREA), scale


def _hue_distance(hues, centre):
    d = np.abs(hues.astype(np.int16) - int(centre))
    return np.minimum(d, 180 - d)


def felt_mask(hsv, felt_bgr):
    """White where the picture is this felt: its hue, nearly as coloured, and from shadowed to a little lit. Tighter
    than the reader's test, which a light wood next to red felt passes: wood is the same hue, but far brighter."""
    hue, sat, val = (int(v) for v in cv2.cvtColor(np.uint8([[felt_bgr]]), cv2.COLOR_BGR2HSV)[0, 0])
    return ((_hue_distance(hsv[..., 0], hue) <= 10) & (hsv[..., 1] >= 0.5 * sat)
            & (hsv[..., 2] >= 0.35 * val) & (hsv[..., 2] <= 1.6 * val + 10)).astype(np.uint8) * 255


def _ringed_by_felt(felt, box, scale):
    """True when felt lies all round this box (in the picture's pixels; felt is a mask at scale)."""
    x, y, w, h = (int(v * scale) for v in box)
    pad = max(3, int(0.6 * max(w, h)))
    x0, y0, x1, y1 = max(x - pad, 0), max(y - pad, 0), min(x + w + pad, felt.shape[1]), min(y + h + pad, felt.shape[0])
    ring = np.ones((y1 - y0, x1 - x0), bool)
    ring[max(y - y0, 0): y - y0 + h, max(x - x0, 0): x - x0 + w] = False
    return (x0, y0, x1, y1) == (x - pad, y - pad, x + w + pad, y + h + pad) and felt[y0:y1, x0:x1][ring].mean() > 0.85 * 255


def find_felt(frame):
    """The felt, from the middle of a picture of the empty table: its hue, colour (blue, green, red), brightness,
    where it is ([left, top, right, bottom] in the picture's pixels), the things lying on it (big and solid, as a
    laptop: walls along its edge are thin), and its mask (at the small picture's scale)."""
    small, scale = _small(frame)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    h, w = hsv.shape[:2]
    middle = small[h // 4: 3 * h // 4, w // 4: 3 * w // 4].reshape(-1, 3)
    middle_hsv = hsv[h // 4: 3 * h // 4, w // 4: 3 * w // 4].reshape(-1, 3)
    # Coloured enough, and not black: "black" against the picture's own brightness, not a fixed level. A short
    # exposure keeps a rolling die sharp but leaves red felt dark (in the 20s), where the dice still stand out well.
    level = float(np.median(middle_hsv[:, 2]))
    coloured = (middle_hsv[:, 1] >= 40) & (middle_hsv[:, 2] >= min(25.0, max(8.0, 0.5 * level)))
    if coloured.mean() < 0.3:
        raise SetupError("The middle of the picture doesn't look like felt: is the camera over the table, "
                         f"and the picture not too dark? (The middle came out at {level:.0f} of 255; "
                         f"{100 * coloured.mean():.0f}% of it looked like felt, and 30% is needed.)")
    hues = middle_hsv[coloured, 0]
    counts = np.bincount(hues, minlength=180).astype(float)
    counts = np.convolve(np.concatenate([counts[-5:], counts, counts[:5]]), np.ones(11), "valid")   # round the circle
    peak = int(np.argmax(counts))
    near = coloured.copy()
    near[coloured] = _hue_distance(hues, peak) <= 8
    bgr = [float(v) for v in np.median(middle[near], axis=0)]
    felt = felt_mask(hsv, bgr)
    closed = cv2.morphologyEx(felt, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))      # fill in specks and dice
    count, labels, stats, _ = cv2.connectedComponentsWithStats(closed)
    if count < 2:
        raise SetupError("Couldn't find the felt.")
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, bw, bh = (int(v) for v in stats[biggest, :4])
    if bw * bh < 0.15 * w * h:
        raise SetupError("The felt fills too little of the picture: bring the camera closer, or point it at the table.")
    # things lying on the felt: big, solid patches that aren't felt inside its box (not thin strips: walls)
    inside = np.zeros_like(felt)
    inside[y:y + bh, x:x + bw] = 255
    others = cv2.bitwise_and(cv2.bitwise_not(cv2.morphologyEx(felt, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))), inside)
    n, _, parts, _ = cv2.connectedComponentsWithStats(others)
    things = [[int(v / scale) for v in parts[i, :4]] for i in range(1, n)
              if parts[i, cv2.CC_STAT_AREA] >= 0.004 * bw * bh
              and min(parts[i, cv2.CC_STAT_WIDTH], parts[i, cv2.CC_STAT_HEIGHT]) >= 0.06 * min(bw, bh)
              # solid, as a laptop is: the walls round the felt join into a thin U that fills little of its box
              and parts[i, cv2.CC_STAT_AREA] >= 0.45 * parts[i, cv2.CC_STAT_WIDTH] * parts[i, cv2.CC_STAT_HEIGHT]]
    hue = int(cv2.cvtColor(np.uint8([[bgr]]), cv2.COLOR_BGR2HSV)[0, 0, 0])
    return {"felt_hue": hue, "felt_bgr": [round(v, 1) for v in bgr], "level": round(max(bgr), 1),
            "felt_box": [int(x / scale), int(y / scale), int((x + bw) / scale), int((y + bh) / scale)],
            "things": things, "mask": felt, "scale": scale}


def _to_reference(box, frame):
    """Picture pixels to the reader's units (a 1920 picture's), as roi is kept."""
    scale = dv.scale_of(frame)
    return [int(round(v / scale)) for v in box]


def _boxes(dice):
    return [{"box": [int(v) for v in cv2.boundingRect(d["contour"])], "value": d["value"]} for d in dice]


def _move_edge(roi, box, scale):
    """The search area with whichever edge loses the least area moved in past this box (picture pixels), or None."""
    x0, y0, x1, y1 = roi
    sx, sy, sw, sh = (v / scale for v in box)
    if sx >= x1 or sy >= y1 or sx + sw <= x0 or sy + sh <= y0:
        return roi                                      # already outside
    pad = 4
    options = [([int(sx + sw + pad), y0, x1, y1], (sx + sw + pad - x0) * (y1 - y0)),         # left edge past it
               ([x0, y0, int(sx - pad), y1], (x1 - sx + pad) * (y1 - y0)),                   # right edge before it
               ([x0, int(sy + sh + pad), x1, y1], (sy + sh + pad - y0) * (x1 - x0)),         # top edge below it
               ([x0, y0, x1, int(sy - pad)], (y1 - sy + pad) * (x1 - x0))]                   # bottom edge above it
    options = [(r, lost) for r, lost in options if r[2] - r[0] > 50 and r[3] - r[1] > 50]
    return min(options, key=lambda o: o[1])[0] if options else None


def leave_out(frame, roi, settings, things=(), felt=None):
    """Move the search area's edges in past the things lying on the felt, then until the reader finds no die in this
    empty picture, each time the edge that loses the least area. A die-like spot with felt all round it is most
    likely a die left on the table: SetupError. Returns (roi, the spots left out, the spots still in)."""
    left_out = []
    scale = dv.scale_of(frame)
    for box in things:
        moved = _move_edge(roi, box, scale)
        if moved is not None and moved != roi:
            roi = moved
            left_out.append({"box": box, "value": None, "thing": True})
    for _ in range(EDGE_TRIES):
        with using({**settings, "roi": roi}):
            spots = _boxes(dv.find_dice(frame))
        if not spots:
            return roi, left_out, []
        if felt is not None and _ringed_by_felt(felt["mask"], spots[0]["box"], felt["scale"]):
            raise SetupError("There's something on the felt that looks like a die: clear the table and measure again.")
        x0, y0, x1, y1 = roi
        sx, sy, sw, sh = (v / scale for v in spots[0]["box"])
        pad = 4
        options = [([int(sx + sw + pad), y0, x1, y1], (sx + sw + pad - x0) * (y1 - y0)),     # left edge past it
                   ([x0, y0, int(sx - pad), y1], (x1 - sx + pad) * (y1 - y0)),               # right edge before it
                   ([x0, int(sy + sh + pad), x1, y1], (sy + sh + pad - y0) * (x1 - x0)),     # top edge below it
                   ([x0, y0, x1, int(sy - pad)], (y1 - sy + pad) * (x1 - x0))]               # bottom edge above it
        options = [(r, lost) for r, lost in options if r[2] - r[0] > 50 and r[3] - r[1] > 50]
        if not options:
            break
        roi = min(options, key=lambda o: o[1])[0]
        left_out.append(spots[0])
    with using({**settings, "roi": roi}):
        still = _boxes(dv.find_dice(frame))
    return roi, left_out, still


def empty_table(frame, settings):
    """Measure the felt and set the search area from a picture of the empty table. Returns the settings found
    (felt_hue, felt_bgr, roi), the felt's brightness, and the spots left out of the search area."""
    felt = find_felt(frame)
    h, w = frame.shape[:2]
    margin = int(WALL_MARGIN * dv.scale_of(frame))
    x0, y0, x1, y1 = felt["felt_box"]
    box = [max(0, x0 - margin), max(0, y0 - margin), min(w, x1 + margin), min(h, y1 + margin)]
    found = {"felt_hue": felt["felt_hue"], "felt_bgr": felt["felt_bgr"]}
    evened = even(frame, {**settings, **found, "roi": None})      # as the reader will see it
    roi, left_out, still = leave_out(evened, _to_reference(box, frame), {**settings, **found}, felt["things"], felt)
    found["roi"] = roi
    return {"settings": found, "level": felt["level"], "left_out": left_out, "still": still}


def _die_spots(frame, settings):
    """The die-sized spots in the search area that aren't felt and have felt round them, biggest first, as
    (contour, area) in the picture's pixels."""
    roi = dv.roi_of(frame) if settings.get("roi") else None
    x0, y0, x1, y1 = roi or [0, 0, frame.shape[1], frame.shape[0]]
    area = frame[y0:y1, x0:x1]
    hsv = cv2.cvtColor(area, cv2.COLOR_BGR2HSV)
    not_felt = cv2.bitwise_not(felt_mask(hsv, settings["felt_bgr"]))
    not_felt = cv2.morphologyEx(not_felt, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    smallest = 150 * dv.scale_of(frame) ** 2
    # each patch on its own: the walls round the felt are one patch with the felt inside it, and the dice inside that
    count, labels, stats, _ = cv2.connectedComponentsWithStats(not_felt)
    spots = []
    for i in range(1, count):
        if stats[i, cv2.CC_STAT_AREA] < smallest / 2:
            continue
        x, y, w, h = stats[i, :4]
        piece = (labels[y:y + h, x:x + w] == i).astype(np.uint8)
        found, _ = cv2.findContours(piece, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        c = max(found, key=cv2.contourArea) + np.array([x, y])
        hull = cv2.convexHull(c)
        a = cv2.contourArea(hull)
        bx, by, bw, bh = cv2.boundingRect(hull)
        if a < smallest or bx <= 1 or by <= 1 or bx + bw >= area.shape[1] - 1 or by + bh >= area.shape[0] - 1:
            continue                                    # a speck, or touching the edge (a wall, the laptop)
        (_, (rw, rh), _) = cv2.minAreaRect(hull)
        if max(rw, rh) / max(min(rw, rh), 1) > 1.8 or a / max(rw * rh, 1) < 0.6:
            continue                                    # not square: not a die seen from above
        spots.append((hull + np.array([x0, y0]), a))
    return sorted(spots, key=lambda s: -s[1])


def _hue_range(hues, felt_hue):
    """The dice's hues, widened a little, as (low, high, centre); low above high when they wrap round through red.
    Taken round the dice's commonest hue, leaving out the felt's: a die's edge shades into the felt, and see-through
    dice show it through."""
    hues = hues[_hue_distance(hues, felt_hue) > 15]
    if len(hues) < 20:
        return None
    counts = np.bincount(hues, minlength=180).astype(float)
    counts = np.convolve(np.concatenate([counts[-7:], counts, counts[:7]]), np.ones(15), "valid")
    peak = int(np.argmax(counts))
    offsets = ((hues.astype(int) - peak + 90) % 180) - 90          # each hue's distance from the peak, signed
    offsets = offsets[np.abs(offsets) <= 30]
    low, high = np.percentile(offsets, 5) - 12, np.percentile(offsets, 95) + 12
    return int(round(peak + low)) % 180, int(round(peak + high)) % 180, peak


def two_dice(frame, settings, empty=None):
    """Measure two dice lying on the felt, then check the reader reads them with what was measured, and that the
    empty picture (if given) still shows no dice. Returns the settings found, what was read, and any warnings."""
    if settings.get("felt_hue") is None or not settings.get("felt_bgr"):
        raise SetupError("Measure the empty table first.")
    # measured as the reader will see them, evened out to the felt (a camera the browser couldn't set)
    frame = even(frame, settings)
    if empty is not None:
        empty = even(empty, settings)
    with using(settings):
        spots = _die_spots(frame, settings)
    if len(spots) < 2:
        raise SetupError("Couldn't find two dice on the felt. Put them apart from each other and from the walls, "
                         "inside the dashed line.")
    (a, area_a), (b, area_b) = spots[:2]
    if max(area_a, area_b) > 2.5 * min(area_a, area_b):
        raise SetupError("The two biggest things on the felt aren't the same size: is something else on the table?")
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    body_hues, body_values, pip_values = [], [], []
    for contour in (a, b):
        inside = np.zeros(frame.shape[:2], np.uint8)
        cv2.drawContours(inside, [contour.astype(np.int32)], -1, 255, cv2.FILLED)
        inside = cv2.erode(inside, np.ones((3, 3), np.uint8))
        pixels = hsv[inside > 0]
        values = pixels[:, 2].astype(np.uint8).reshape(-1, 1)
        split, _ = cv2.threshold(values, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)   # pips from the die's body
        body = pixels[pixels[:, 2] <= split]           # the split is the top of the darker side
        pips = pixels[(pixels[:, 2] > split) & (pixels[:, 1] < 90)]
        if len(body) < 20:
            raise SetupError("A die came out nearly all white: the picture is too bright. Measure the empty table again.")
        body_hues.append(body[body[:, 1] >= 60][:, 0])
        body_values.append(float(np.median(body[:, 2])))
        pip_values.append(float(np.median(pips[:, 2])) if len(pips) else 255.0)
    warnings = []
    hues = _hue_range(np.concatenate(body_hues), settings["felt_hue"])
    if hues is None:                                # white, black or felt-coloured dice: found as not-felt spots only
        hue_low, hue_high, centre = settings.get("hue_low", 35), settings.get("hue_high", 100), None
        warnings.append("The dice have hardly any colour of their own, or the felt's: they're found as not-felt spots.")
    else:
        hue_low, hue_high, centre = hues
    body_v, pip_v = float(np.mean(body_values)), float(np.mean(pip_values))
    if pip_v - body_v < 25:
        warnings.append("The pips are hardly brighter than the dice: more light, or less glare, would help.")
    die_area = (area_a + area_b) / 2 / dv.scale_of(frame) ** 2      # in a 1920 picture's pixels, as the sizes are kept
    found = {"hue_low": hue_low, "hue_high": hue_high, "white_level": int(np.clip((body_v + pip_v) / 2, 60, 240)),
             "min_die_area": int(np.clip(0.6 * die_area, 50, 20000)),
             "two_dice_area": int(np.clip(3.0 * die_area, 500, 40000))}
    # the dice model only knows the dice it learned from: keep it for dice of the same colour as the defaults'
    from . import camera_profiles
    defaults = camera_profiles.defaults()
    d_low, d_high = defaults.get("hue_low", 0), defaults.get("hue_high", 179)
    found["use_model"] = bool(bool(defaults.get("use_model", True)) and centre is not None and d_low <= centre <= d_high)
    left_out = []
    if empty is not None:
        roi, left_out, still = leave_out(empty, settings["roi"], {**settings, **found})
        found["roi"] = roi
        if still:
            warnings.append("The empty table still shows something the reader takes for a die.")
    with using({**settings, **found}):
        dice = _boxes(dv.find_dice(frame))
    read = [d["value"] for d in dice]
    return {"settings": found, "dice": dice, "read": len(read) == 2 and None not in read,
            "left_out": left_out, "warnings": warnings}
