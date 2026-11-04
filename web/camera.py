"""
Reading the dice. dice_vision.py watches the pictures; each throw it reads is rolled (or logged, on the
tracker) for whoever pressed Start camera, as the desktop apps do, and their open pages hear about it
straight away.

The pictures come from the page's own camera (the device the page is on sends them, see PagePictures), or
from a webcam plugged into the server (CRAPS_CAMERA: a number, 0 is the first, a device such as
/dev/video0, or "demo" for a pretend camera that throws drawn dice).

Only the accounts in CRAPS_CAMERA_USERS may use it: with open sign-up, strangers mustn't be able to
start the reader or watch its picture.
"""

import json
import logging
import os
import queue
import threading
import time

from . import camera_profiles, config, events, tables

log = logging.getLogger("craps.camera")

PAGE_WAIT = 10               # seconds without a picture from the page before its reading is stopped (page closed)
MAX_PICTURE = 8_000_000      # bytes: a full HD JPEG is well under this
UNCLEAR_WINDOW = 120         # seconds: dice typed this soon after a read the camera couldn't make get its pictures
SETTINGS_FILE = os.path.join(config.DATA_DIR, "dice_settings.json")
# what the tuning page may change, and the range each may take
LIMITS = {"hue_low": (0, 179), "hue_high": (0, 179), "sat_low": (0, 255), "val_low": (0, 255),
          "white_level": (0, 255), "min_die_area": (50, 20000), "two_dice_area": (500, 40000),
          "exposure_linux": (1, 10000), "exposure": (-13, 0), "focus": (0, 255), "gain": (0, 255),
          "rotate": (0, 270), "white_balance": (2000, 7500)}

try:
    import cv2
    import numpy as np

    import dice_vision as dv
    IMPORT_ERROR = ""
except ImportError as error:          # opencv-python-headless and numpy aren't installed
    dv = None
    IMPORT_ERROR = str(error)


class CameraError(Exception):
    """Why the camera can't do what was asked. gone: the reader isn't running at all."""

    def __init__(self, message, gone=False):
        super().__init__(message)
        self.gone = gone


def setup():
    """Keep the camera's files in the data folder, and use the settings saved there."""
    if dv is None:
        return
    dv.UNCLEAR_DIR = os.path.join(config.DATA_DIR, "unclear_frames")
    if os.path.exists(SETTINGS_FILE):
        dv.load_settings(SETTINGS_FILE)
    dv.load_model(os.path.join(config.DATA_DIR, "dice_model.npz"))
    _set_defaults()


def _set_defaults():
    """What a camera with no settings of its own reads with: the ones in data/dice_settings.json."""
    try:
        with open(SETTINGS_FILE) as f:
            white_balance = json.load(f).get("white_balance")
    except (OSError, ValueError, AttributeError):
        white_balance = None
    camera_profiles.set_defaults(dv.current_settings(), page_camera(), white_balance)


def allowed(user):
    return dv is not None and user["email"] in config.CAMERA_USERS


def page_camera():
    """The camera controls the page sets on its own camera before sending pictures, from "page_camera" in
    data/dice_settings.json, in the browser's names (exposureMode, exposureTime, focusMode, focusDistance, ...).
    The reader was tuned on darker pictures than a webcam's automatic exposure gives over the dark felt: left
    automatic, the dice burn out white. Read afresh each time, so a change there needs no restart."""
    try:
        with open(SETTINGS_FILE) as f:
            values = json.load(f).get("page_camera", {})
    except (OSError, ValueError, AttributeError):
        return {}
    return values if isinstance(values, dict) else {}


class Evener:
    """Evens out a camera's pictures to the felt measured when it was set up (felt_bgr at felt_level), for cameras
    the browser can't set: each picture's colours scaled so the felt comes out the same whatever the camera's own
    exposure and white balance do. Measured about once a second, and changed only when it's off by a tenth or more:
    each change looks like movement to the reader. A camera whose controls the page set is near enough already."""

    EVERY = 1.0

    def __init__(self, settings):
        self.settings = settings
        self.gains = np.ones(3)
        self.table = None
        self.next = 0.0

    def apply(self, frame):
        from . import camera_setup
        now = time.monotonic()
        if now >= self.next:
            self.next = now + self.EVERY
            gains = camera_setup.even_gains(frame, self.settings["felt_bgr"], self.settings.get("felt_level") or 88,
                                            self.settings.get("roi"))
            if gains is not None and np.max(np.abs(gains / self.gains - 1)) >= 0.1:
                self.gains = gains
                self.table = None if np.all(np.abs(gains - 1) < 0.06) else camera_setup.gain_table(gains)
        return frame if self.table is None else cv2.LUT(frame, self.table)

    @property
    def amount(self):
        """How much the pictures are being brightened (above 1) or darkened, or None when they're left as they are."""
        return None if self.table is None else round(float(np.mean(self.gains)), 2)


class PagePictures:
    """Stands in for a webcam when the pictures come from the page: read() hands over the newest one the page
    has sent (an older one not yet read is skipped, as a webcam's buffer of one does). When none comes for
    PAGE_WAIT seconds the page has gone (closed, or asleep), and read() says the feed is lost."""

    def __init__(self, wait=None):
        self.wait = PAGE_WAIT if wait is None else wait
        self.ready = threading.Condition()
        self.frame = None
        self.closed = False

    def push(self, frame):
        with self.ready:
            self.frame = frame
            self.ready.notify()

    def read(self):
        with self.ready:
            self.ready.wait_for(lambda: self.frame is not None or self.closed, timeout=self.wait)
            frame, self.frame = self.frame, None
        return frame is not None, frame

    def isOpened(self):
        return True

    def release(self):
        with self.ready:
            self.closed = True
            self.ready.notify()


def save_crops(throw_id, crops):
    if not crops or len(crops) != 2:
        return
    try:
        os.makedirs(config.CROP_DIR, exist_ok=True)
        for side, png in zip(("left", "right"), crops):
            with open(os.path.join(config.CROP_DIR, f"{throw_id}_{side}.png"), "wb") as f:
                f.write(png)
    except OSError:
        log.exception("Couldn't save the dice pictures")


class CameraService:
    def __init__(self):
        self.lock = threading.Lock()
        self.camera = None
        self.owner = None            # who the throws go to: user, client (their page), player, mode and settings
        self.pending = {}            # (user id, player id) -> (time, crops) of the last read it couldn't make
        self.error = ""
        self._pump = None
        self.pictures = None         # PagePictures when the page sends the pictures, None for the server's webcam
        self.profile = None          # the settings the page's camera reads with (camera_profiles), while it reads
        self.evener = None           # evens out its pictures, when it was set up (felt_bgr)

    # ---------------------------------------------------------------- starting and stopping
    def _source(self):
        source = config.CAMERA_SOURCE
        if source == "demo":
            from .camera_demo import DemoCapture
            return None, DemoCapture()
        return (int(source) if source.isdigit() else source), None

    def start(self, user, client, binding, from_page=False, camera_name=""):
        """Start reading for this user: from the pictures their page sends (from_page), or from the server's webcam.
        Already reading for them, the throws go to this page and its player now (and from_page switches over)."""
        with self.lock:
            if self.camera is not None and self.owner and self.owner["user_id"] != user["id"]:
                raise CameraError(f"{self.owner['user_name']} is using the camera.")
            same_camera = not from_page or (self.profile or {}).get("_camera") == camera_profiles.camera_key(camera_name)
            if self.camera is not None and (self.pictures is not None) == from_page and same_camera:
                self.owner = self._owner(user, client, binding)
                return
        if self.camera is not None:
            self.stop(user, quiet=True)          # switching between the server's webcam and the page's pictures
        with self.lock:
            if self.camera is not None:          # someone started it in between
                raise CameraError(f"{self.owner['user_name']} is using the camera.")
            if from_page:
                # this camera's own settings, or the defaults: one reader at a time, so they're the reader's now
                values, _ = camera_profiles.effective(user["id"], camera_name)
                dv.apply_settings(camera_profiles.reader_part(values))
                self.profile = {**values, "_camera": camera_profiles.camera_key(camera_name)}
                self.evener = Evener(values) if values.get("felt_bgr") else None
                pictures = PagePictures()
                # the page sends its picture as it shows it, turned by its own Turn button
                camera = dv.DiceCamera(None, pictures, turn_pictures=False)
            else:
                if not config.CAMERA_SOURCE:
                    raise CameraError("The server has no webcam of its own (CRAPS_CAMERA isn't set).")
                pictures = None
                index, capture = self._source()
                camera = dv.DiceCamera(index, capture)
            if not camera.start():
                self.error = camera.error
                if from_page:
                    dv.apply_settings(camera_profiles.reader_part(camera_profiles.defaults()))
                    self.profile = None
                raise CameraError(camera.error)
            self.camera, self.pictures, self.error = camera, pictures, ""
            self.owner = self._owner(user, client, binding)
            self._pump = threading.Thread(target=self._run, args=(camera,), daemon=True)
            self._pump.start()
        log.info("Camera started for %s", user["email"])

    def _owner(self, user, client, binding):
        return {"user_id": user["id"], "user_name": user["name"] or user["email"], "client": client, **binding}

    def update(self, user, client, binding):
        """The camera's owner switched player, tab, dice set or position: throws go there now."""
        with self.lock:
            if self.owner is None or self.owner["user_id"] != user["id"]:
                raise CameraError("The camera isn't running for you.")
            self.owner = self._owner(user, client, binding)

    def stop(self, user=None, quiet=False):
        with self.lock:
            if user is not None and self.owner and self.owner["user_id"] != user["id"]:
                raise CameraError(f"{self.owner['user_name']} is using the camera.")
            camera, owner, pictures = self.camera, self.owner, self.pictures
            self.camera = self.owner = self.pictures = None
        if pictures is not None:
            pictures.release()                   # so the reader isn't left waiting for a picture
        if camera is not None:
            camera.stop()
        if pictures is not None:
            # back to the defaults, for the server's webcam and the next camera
            dv.apply_settings(camera_profiles.reader_part(camera_profiles.defaults()))
            self.profile = None
            self.evener = None
        if owner and not quiet:
            events.publish(owner["user_id"], "camera", running=False)

    def use_profile(self, user, camera_name):
        """This camera's settings changed: if it's the one reading for this user, it reads with them now."""
        with self.lock:
            profile, owner = self.profile, self.owner
            if profile is None or owner is None or owner["user_id"] != user["id"] \
                    or profile.get("_camera") != camera_profiles.camera_key(camera_name):
                return
            values, _ = camera_profiles.effective(user["id"], camera_name)
            dv.apply_settings(camera_profiles.reader_part(values))
            self.profile = {**values, "_camera": profile["_camera"]}
            self.evener = Evener(values) if values.get("felt_bgr") else None

    def picture(self, user, client, jpeg):
        """One picture from the page's camera. CameraError says why it isn't wanted: not reading
        (gone=True: start again), or another page or person is."""
        pictures, owner = self.pictures, self.owner
        if pictures is None or owner is None:
            raise CameraError("The dice reader isn't running.", gone=True)
        if owner["user_id"] != user["id"]:
            raise CameraError(f"{owner['user_name']} is using the camera.")
        if owner["client"] != client:
            raise CameraError("The dice are being read from another page.")
        if len(jpeg) > MAX_PICTURE:
            raise CameraError("That picture is too big.")
        frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise CameraError("That isn't a picture.")
        evener = self.evener
        if evener is not None:
            frame = evener.apply(frame)
        pictures.push(frame)
        camera = self.camera
        if camera is None:
            return {"status": "stopped", "fps": None, "dice": [], "roi": None, "size": None}
        # what the reader found last and where it looks, in this picture's pixels, for the page to draw over
        # its own picture (a picture or so behind, as the reader works on the one before)
        return {"status": camera.status, "fps": round(camera.fps, 1) if camera.fps else None,
                "dice": camera.seen_boxes(), "roi": dv.roi_of(frame), "size": [frame.shape[1], frame.shape[0]],
                "evened": evener.amount if evener is not None else None}

    # ---------------------------------------------------------------- what the camera sees
    def _run(self, camera):
        while self.camera is camera:
            try:
                kind, payload = camera.events.get(timeout=0.5)
            except queue.Empty:
                continue
            if self.camera is not camera:
                break                    # stopped while waiting: its last news (the feed ending) is no news
            try:
                if kind == "throw":
                    self._throw(*payload["dice"], payload.get("crops"))
                elif kind == "unclear":
                    self._unclear(payload)
                elif kind == "error":
                    self.error = ("The camera's pictures stopped coming (the page was closed or went to sleep)."
                                  if self.pictures is not None else str(payload))
                    owner = self.owner
                    self.stop()
                    if owner:
                        events.publish(owner["user_id"], "camera_error", message=self.error)
            except Exception:
                log.exception("Couldn't handle a camera %s", kind)

    def _throw(self, die1, die2, crops):
        owner = self.owner
        if owner is None:
            return
        user_id, player_id = owner["user_id"], owner["player_id"]
        self.pending.pop((user_id, player_id), None)
        read = f"{die1}-{die2}"
        try:
            if owner["mode"] == "tracker":
                said, throw_id = tables.log_throw(user_id, player_id, die1, die2, owner["back_wall"], owner,
                                                  owner["voice"], source="camera", camera_read=read)
            else:
                said, throw_id = tables.roll(user_id, player_id, die1, die2, owner["back_wall"], owner,
                                             owner["voice"], owner["buy_in"], source="camera", camera_read=read,
                                             premium=owner.get("premium", False), personal=owner.get("personal", True))
        except tables.TableError:
            events.publish(user_id, "camera_error", message="The player the camera was throwing for is gone.")
            return
        save_crops(throw_id, crops)
        events.publish(user_id, "camera_throw", player_id=player_id, dice=[die1, die2], call=said["call"],
                       reaction=said["reaction"], client=owner["client"],
                       message=f"Camera read {die1}-{die2}. Wrong? Tap the right dice (left die first) and Fix last.")

    def _unclear(self, payload):
        owner = self.owner
        if owner is None:
            return
        self.pending[(owner["user_id"], owner["player_id"])] = (time.time(), payload.get("crops") or [])
        events.publish(owner["user_id"], "camera_unclear", player_id=owner["player_id"], client=owner["client"],
                       message=payload["message"].replace("type the throw in", "tap the dice in"))

    def take_unclear(self, user_id, player_id):
        """For dice typed in by hand: ("unclear", the pictures) when the camera just missed this throw."""
        seen = self.pending.pop((user_id, player_id), None)
        if seen and time.time() - seen[0] < UNCLEAR_WINDOW:
            return "unclear", seen[1]
        return "", None

    # ---------------------------------------------------------------- for the page
    def status(self, user):
        camera, owner = self.camera, self.owner
        out = {"allowed": allowed(user), "running": camera is not None, "error": self.error,
               "from_page": self.pictures is not None}
        if not out["allowed"]:
            return out
        if dv is not None:
            out["model"] = dv.MODEL_STATUS
        if owner:
            out.update(owner=owner["user_name"], mine=owner["user_id"] == user["id"], client=owner["client"],
                       player_id=owner["player_id"], mode=owner["mode"])
        if camera is not None:
            out.update(status=camera.status, view=camera.view,
                       resolution=list(camera.resolution) if camera.resolution else None,
                       fps=round(camera.fps, 1) if camera.fps else None)
        return out

    def preview(self):
        camera = self.camera
        return camera.get_preview_jpeg() if camera is not None else None

    def mask(self):
        """The newest picture as the reader's mask: white where it could find a die."""
        camera = self.camera
        return camera.mask_jpeg() if camera is not None else None

    # ---------------------------------------------------------------- tuning
    def settings(self):
        values = dv.current_settings()
        values["linux"] = dv.LINUX
        values["effective_exposure"] = dv.linux_exposure() if dv.LINUX else dv.EXPOSURE
        values["page_camera"] = page_camera()
        return values

    def tune(self, values, view=None, save=False):
        """Change the reading settings live; with save, keep them in data/dice_settings.json."""
        names = dv._SAVED_NAMES
        for key, value in values.items():
            if key not in LIMITS:
                continue
            low, high = LIMITS[key]
            if value is not None:
                if not isinstance(value, (int, float)):
                    raise CameraError(f"{key} must be a number.")
                value = int(min(high, max(low, value)))
                if key == "rotate":
                    value = round(value / 90) * 90          # quarter turns only
            setattr(dv, names[key], value)
        camera = self.camera
        if camera is not None:
            if view in ("normal", "mask"):
                camera.view = view
            camera.reapply()
        if save:
            try:
                with open(SETTINGS_FILE) as f:
                    saved = json.load(f)
            except (OSError, ValueError):
                saved = {}
            saved.update({k: v for k, v in dv.current_settings().items() if k != "roi" or v is not None})
            os.makedirs(config.DATA_DIR, exist_ok=True)
            with open(SETTINGS_FILE, "w") as f:
                json.dump(saved, f, indent=2)
            if self.pictures is None:
                _set_defaults()
        return self.settings()


service = CameraService()
