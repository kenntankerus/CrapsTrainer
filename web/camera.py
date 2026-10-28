"""
The webcam at the table, run by the server. dice_vision.py watches it; each throw it reads is rolled
(or logged, on the tracker) for whoever pressed Start camera, as the desktop apps do, and their open
pages hear about it straight away.

Only the accounts in CRAPS_CAMERA_USERS may use it: with open sign-up, strangers mustn't be able to
start your camera or watch its picture. CRAPS_CAMERA says which camera: a number (0 is the first),
a device such as /dev/video0, or "demo" for a pretend camera that throws drawn dice.
"""

import json
import logging
import os
import queue
import threading
import time

from . import config, events, tables

log = logging.getLogger("craps.camera")

UNCLEAR_WINDOW = 120         # seconds: dice typed this soon after a read the camera couldn't make get its pictures
SETTINGS_FILE = os.path.join(config.DATA_DIR, "dice_settings.json")
# what the tuning page may change, and the range each may take
LIMITS = {"hue_low": (0, 179), "hue_high": (0, 179), "sat_low": (0, 255), "val_low": (0, 255),
          "white_level": (0, 255), "min_die_area": (50, 20000), "two_dice_area": (500, 40000),
          "exposure_linux": (1, 10000), "exposure": (-13, 0), "focus": (0, 255), "gain": (0, 255)}

try:
    import dice_vision as dv
    IMPORT_ERROR = ""
except ImportError as error:          # opencv-python-headless and numpy aren't installed
    dv = None
    IMPORT_ERROR = str(error)


class CameraError(Exception):
    """Why the camera can't do what was asked."""


def setup():
    """Keep the camera's files in the data folder, and use the settings saved there."""
    if dv is None:
        return
    dv.UNCLEAR_DIR = os.path.join(config.DATA_DIR, "unclear_frames")
    if os.path.exists(SETTINGS_FILE):
        dv.load_settings(SETTINGS_FILE)
    dv.load_model(os.path.join(config.DATA_DIR, "dice_model.npz"))


def allowed(user):
    return config.CAMERA_SOURCE != "" and dv is not None and user["email"] in config.CAMERA_USERS


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

    # ---------------------------------------------------------------- starting and stopping
    def _source(self):
        source = config.CAMERA_SOURCE
        if source == "demo":
            from .camera_demo import DemoCapture
            return None, DemoCapture()
        return (int(source) if source.isdigit() else source), None

    def start(self, user, client, binding):
        with self.lock:
            if self.camera is not None:
                if self.owner and self.owner["user_id"] != user["id"]:
                    raise CameraError(f"{self.owner['user_name']} is using the camera.")
                self.owner = self._owner(user, client, binding)
                return
            index, capture = self._source()
            camera = dv.DiceCamera(index, capture)
            if not camera.start():
                self.error = camera.error
                raise CameraError(camera.error)
            self.camera, self.error = camera, ""
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

    def stop(self, user=None):
        with self.lock:
            if user is not None and self.owner and self.owner["user_id"] != user["id"]:
                raise CameraError(f"{self.owner['user_name']} is using the camera.")
            camera, owner = self.camera, self.owner
            self.camera = self.owner = None
        if camera is not None:
            camera.stop()
        if owner:
            events.publish(owner["user_id"], "camera", running=False)

    # ---------------------------------------------------------------- what the camera sees
    def _run(self, camera):
        while self.camera is camera:
            try:
                kind, payload = camera.events.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if kind == "throw":
                    self._throw(*payload["dice"], payload.get("crops"))
                elif kind == "unclear":
                    self._unclear(payload)
                elif kind == "error":
                    self.error = str(payload)
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
                call, throw_id = tables.log_throw(user_id, player_id, die1, die2, owner["back_wall"], owner,
                                                  owner["voice"], source="camera", camera_read=read)
            else:
                call, throw_id = tables.roll(user_id, player_id, die1, die2, owner["back_wall"], owner,
                                             owner["voice"], owner["buy_in"], source="camera", camera_read=read)
        except tables.TableError:
            events.publish(user_id, "camera_error", message="The player the camera was throwing for is gone.")
            return
        save_crops(throw_id, crops)
        events.publish(user_id, "camera_throw", player_id=player_id, dice=[die1, die2], call=call,
                       voice=owner["voice"], client=owner["client"],
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
        out = {"allowed": allowed(user), "running": camera is not None, "error": self.error}
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

    # ---------------------------------------------------------------- tuning
    def settings(self):
        values = dv.current_settings()
        values["linux"] = dv.LINUX
        values["effective_exposure"] = dv.linux_exposure() if dv.LINUX else dv.EXPOSURE
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
        return self.settings()


service = CameraService()
