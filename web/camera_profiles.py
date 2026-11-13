"""
The dice reader's settings for each account's cameras.

Every camera sees a table differently (where the felt is, how bright, what colour the dice come out), so a
camera's settings are kept per account and per camera, the camera named as the browser names it ("Logitech
BRIO (046d:085e)"). A camera with none saved reads with the defaults: data/dice_settings.json, tuned for the
owner's Brio over the owner's table.

A profile holds the reader's settings (dice_vision's names, as in dice_settings.json), page_camera and
white_balance (the controls the page sets on its camera), and felt_bgr and felt_level (the felt's colour and
brightness, for evening out the pictures of a camera the browser can't control).
"""

import json
import time

from . import db

FELT_LEVEL = 88               # the felt's brightness (the brightest of its blue, green, red) the reader was tuned on

# what each setting may be: (kind, low, high); None is allowed where it turns the setting off
NUMBERS = {
    "hue_low": (int, 0, 179), "hue_high": (int, 0, 179), "sat_low": (int, 0, 255), "val_low": (int, 0, 255),
    "white_level": (int, 0, 255), "min_die_area": (int, 50, 20000), "two_dice_area": (int, 500, 40000),
    "felt_hue": (int, 0, 179), "tilt": (float, 0, 0.2), "white_balance": (int, 2000, 7500),
    "felt_level": (int, 20, 220), "felt_val_low": (int, 0, 255),
}
OFF_ALLOWED = {"felt_hue", "tilt", "white_balance", "roi", "nadir", "felt_bgr"}
READER_KEYS = ["hue_low", "hue_high", "sat_low", "val_low", "white_level", "min_die_area", "two_dice_area",
               "roi", "felt_hue", "nadir", "tilt", "use_model", "felt_val_low"]
KEYS = READER_KEYS + ["page_camera", "white_balance", "felt_bgr", "felt_level"]
# the camera controls a page may be told to set, in the browser's names
PAGE_MODES = {"exposureMode", "focusMode", "whiteBalanceMode"}
PAGE_NUMBERS = {"exposureTime", "exposureCompensation", "focusDistance", "colorTemperature", "brightness",
                "contrast", "saturation", "sharpness"}

_defaults = {}


def set_defaults(reader_settings, page_camera, white_balance):
    """The settings a camera with none of its own reads with (from data/dice_settings.json)."""
    global _defaults
    _defaults = {key: reader_settings.get(key) for key in READER_KEYS}
    _defaults["use_model"] = reader_settings.get("use_model", True) is not False
    _defaults.update(page_camera=dict(page_camera or {}), white_balance=white_balance, felt_bgr=None,
                     felt_level=FELT_LEVEL)


def defaults():
    return json.loads(json.dumps(_defaults))          # a copy, lists and all


def _number(key, value):
    kind, low, high = NUMBERS[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number.")
    if not low <= value <= high:
        raise ValueError(f"{key} must be from {low} to {high}.")
    return int(round(value)) if kind is int else float(value)


def _points(key, value, count):
    if not isinstance(value, list) or len(value) != count or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 4000 for v in value):
        raise ValueError(f"{key} must be {count} numbers from 0 to 4000.")
    return [int(round(v)) for v in value]


def clean(values):
    """The settings checked and tidied, or ValueError saying what's wrong. Settings left out keep their defaults."""
    if not isinstance(values, dict):
        raise ValueError("The settings must be an object.")
    out = {}
    for key, value in values.items():
        if key not in KEYS:
            raise ValueError(f"There's no setting called {key}.")
        if value is None and key in OFF_ALLOWED:
            out[key] = None
        elif key in NUMBERS:
            out[key] = _number(key, value)
        elif key == "use_model":
            if not isinstance(value, bool):
                raise ValueError("use_model must be true or false.")
            out[key] = value
        elif key == "roi":
            roi = _points(key, value, 4)
            if roi[2] <= roi[0] or roi[3] <= roi[1]:
                raise ValueError("roi is [left, top, right, bottom], with right past left and bottom below top.")
            out[key] = roi
        elif key == "nadir":
            out[key] = _points(key, value, 2)
        elif key == "felt_bgr":
            if not isinstance(value, list) or len(value) != 3 or not all(
                    isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 255 for v in value):
                raise ValueError("felt_bgr must be 3 numbers from 0 to 255 (blue, green, red).")
            out[key] = [round(float(v), 1) for v in value]
        elif key == "page_camera":
            if not isinstance(value, dict):
                raise ValueError("page_camera must be an object of the browser's camera controls.")
            page = {}
            for name, setting in value.items():
                if name in PAGE_MODES and setting in ("manual", "continuous"):
                    page[name] = setting
                elif name in PAGE_NUMBERS and isinstance(setting, (int, float)) and not isinstance(setting, bool) \
                        and 0 <= setting <= 100000:
                    page[name] = setting
                else:
                    raise ValueError(f"page_camera can't have {name}: {setting!r}.")
            out[key] = page
        else:
            raise ValueError(f"{key} isn't a setting this can take.")
    return out                        # hue_low above hue_high is allowed: the dice's hues wrap round through red


def camera_key(camera):
    name = " ".join(str(camera or "").split())[:120]
    return name or "camera"


def saved(user_id, camera):
    row = db.one("SELECT settings FROM camera_profiles WHERE user_id = ? AND camera = ?", user_id, camera_key(camera))
    return json.loads(row["settings"]) if row else None


def effective(user_id, camera):
    """(the settings this camera reads with, whether they're its own)."""
    own = saved(user_id, camera)
    values = defaults()
    values.update(own or {})
    return values, own is not None


def save(user_id, camera, values):
    values = clean(values)
    with db.transaction() as conn:
        conn.execute("INSERT INTO camera_profiles (user_id, camera, settings, updated) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT (user_id, camera) DO UPDATE SET settings = excluded.settings, updated = excluded.updated",
                     (user_id, camera_key(camera), json.dumps(values), time.time()))
    return effective(user_id, camera)


def forget(user_id, camera):
    with db.transaction() as conn:
        conn.execute("DELETE FROM camera_profiles WHERE user_id = ? AND camera = ?", (user_id, camera_key(camera)))
    return effective(user_id, camera)


def reader_part(values):
    """The part of a profile the reader itself uses (dice_vision's settings)."""
    return {key: values[key] for key in READER_KEYS if key in values}
