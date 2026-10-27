"""
Dice camera test
----------------
Step 1 of webcam logging: can the computer read two green dice from above?
This script does NOT log anything yet. It just shows what the camera sees,
draws the pip count on each die, and prints a result when the dice stop moving.

Setup:   pip install opencv-python numpy
Run:     python dice_camera_test.py          (or: python dice_camera_test.py 1  to use camera #1)
Keys:    q = quit,  m = green mask view,  p = pip view (white spots found inside dice),  n = normal view
         -  = darker image,  =  = brighter image  (switches the camera to manual exposure)
         s  = SAVE the current slider and exposure settings so dice_tracker_v2.py uses them
         c  = capture the raw frame and green mask to capture_frame.png / capture_mask.png

How it works:
  1. Find green pixels (the dice) using a color range in HSV color space.
  2. Each big green blob is one die.
  3. Inside each die, find the white blobs. Each white blob is one pip.
  4. When nothing has moved for a moment and exactly two dice are found
     with 1-6 pips each, print the result once.
"""

import json
import os
import sys
from collections import Counter, deque
import cv2
import numpy as np

import dice_vision as dv

CAMERA_INDEX = int(sys.argv[1]) if len(sys.argv) > 1 else 1
STILL_FRAMES_NEEDED = 12      # frames of no movement before we trust a reading
MOTION_THRESHOLD = 1.5        # average pixel change that counts as "moving"
PREVIEW_W, PREVIEW_H = 800, 450       # size of the picture on screen (make smaller if it runs off your screen)
EXPOSURE = -2     # once the - and = keys find a good brightness, type that printed number here (e.g. -6) so it's used every run
FRAME_WIDTH, FRAME_HEIGHT = 1920, 1080   # 1080p. For 4K use 3840, 2160 (needs a USB 3 port)

WINDOW = "Dice camera test"


def nothing(_):
    pass


SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dice_settings.json")


def save_settings(frame_width, exposure):
    """Write the current slider values to dice_settings.json, which dice_vision.py reads."""
    settings = {
        "hue_low": cv2.getTrackbarPos("Hue low", WINDOW),
        "hue_high": cv2.getTrackbarPos("Hue high", WINDOW),
        "sat_low": cv2.getTrackbarPos("Sat low", WINDOW),
        "val_low": cv2.getTrackbarPos("Val low", WINDOW),
        "min_die_area": cv2.getTrackbarPos("Min die area", WINDOW),
        "white_level": cv2.getTrackbarPos("White (pips)", WINDOW),
        "exposure": exposure,
    }
    try:
        with open(SETTINGS_FILE) as f:
            settings = {**json.load(f), **settings}      # keep settings this tool has no slider for, like "roi"
    except (OSError, ValueError):
        pass
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)
    print(f"Saved to {SETTINGS_FILE}:")
    print(json.dumps(settings))


def make_sliders():
    """Sliders so you can tune the green color range live."""
    cv2.namedWindow(WINDOW)
    cv2.createTrackbar("Hue low", WINDOW, dv.HUE_LOW, 179, nothing)
    cv2.createTrackbar("Hue high", WINDOW, dv.HUE_HIGH, 179, nothing)
    cv2.createTrackbar("Sat low", WINDOW, dv.SAT_LOW, 255, nothing)
    cv2.createTrackbar("Val low", WINDOW, dv.VAL_LOW, 255, nothing)
    cv2.createTrackbar("Min die area", WINDOW, dv.MIN_DIE_AREA, 20000, nothing)
    cv2.createTrackbar("White (pips)", WINDOW, dv.WHITE_LEVEL, 255, nothing)


def find_dice(frame):
    """Run dice_vision's detector with the slider values. Returns (list of (contour, pip_count), green_mask)."""
    dv.HUE_LOW = cv2.getTrackbarPos("Hue low", WINDOW)
    dv.HUE_HIGH = cv2.getTrackbarPos("Hue high", WINDOW)
    dv.SAT_LOW = cv2.getTrackbarPos("Sat low", WINDOW)
    dv.VAL_LOW = cv2.getTrackbarPos("Val low", WINDOW)
    dv.MIN_DIE_AREA = max(cv2.getTrackbarPos("Min die area", WINDOW), 50)
    dv.WHITE_LEVEL = cv2.getTrackbarPos("White (pips)", WINDOW)

    found = dv.find_dice(frame)
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, np.array([dv.HUE_LOW, dv.SAT_LOW, dv.VAL_LOW]), np.array([dv.HUE_HIGH, 255, 255]))
    return [(d["contour"], d["pips"]) for d in found], green


def main():
    # CAP_DSHOW is the Windows camera backend; it starts faster and has fewer quirks
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print("Could not open the camera. Try a different number: python dice_camera_test.py 1")
        return
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))   # lets the Brio send high resolutions
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)   # no refocusing when dice tumble through (ignored if unsupported)
    exposure = None       # None = camera chooses; the - and = keys switch to manual
    if EXPOSURE is not None:
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
        cap.set(cv2.CAP_PROP_EXPOSURE, EXPOSURE)
        exposure = EXPOSURE
    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"Camera is running at {actual_w} x {actual_h}")

    make_sliders()
    view = "normal"
    previous_gray = None
    still_count = 0
    armed = True          # becomes True again after the dice move, so we only report once per throw
    recent = deque(maxlen=STILL_FRAMES_NEEDED)   # what we read on each recent frame

    while True:
        ok, frame = cap.read()
        if not ok:
            print("Lost the camera feed.")
            break

        # --- motion detection: compare this frame to the previous one
        gray_small = cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (9, 9), 0)
        if previous_gray is not None:
            motion = float(np.mean(cv2.absdiff(gray_small, previous_gray)))
            if motion > MOTION_THRESHOLD:
                still_count = 0
                armed = True
            else:
                still_count += 1
        previous_gray = gray_small

        raw_frame = frame.copy()
        dice, green_mask = find_dice(frame)

        # --- draw what we found
        for contour, pips in dice:
            x, y, w, h = cv2.boundingRect(contour)
            valid = 1 <= pips <= 6
            color = (0, 255, 0) if valid else (0, 0, 255)
            cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
            cv2.putText(frame, str(pips), (x, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)

        # --- remember what each frame read (dice sorted left to right)
        if still_count == 0:
            recent.clear()
        values = [pips for _, pips in sorted(dice, key=lambda d: cv2.boundingRect(d[0])[0])]
        recent.append(tuple(values))

        # --- once the dice have settled, take a vote across the recent frames,
        #     so one flickery frame can't give a wrong answer
        if still_count >= STILL_FRAMES_NEEDED and armed:
            reading, votes = Counter(recent).most_common(1)[0]
            agreement = votes / len(recent)
            if len(reading) == 2 and all(1 <= v <= 6 for v in reading) and agreement >= 0.7:
                print(f"Throw read: {reading[0]}-{reading[1]}  (total {sum(reading)})")
            else:
                print(f"Dice stopped but the reading is unclear. Frame votes: {dict(Counter(recent))}")
            armed = False

        status = "settled" if still_count >= STILL_FRAMES_NEEDED else "moving"
        cv2.putText(frame, f"{len(dice)} dice | {status}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)

        shown = {"normal": frame, "mask": green_mask, "pips": green_mask}[view]
        cv2.imshow(WINDOW, cv2.resize(shown, (PREVIEW_W, PREVIEW_H)))   # shrunk so it fits your screen
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key == ord("m"):
            view = "mask"
        elif key == ord("p"):
            view = "pips"
        elif key == ord("n"):
            view = "normal"
        elif key == ord("s"):
            save_settings(frame.shape[1], exposure)
        elif key == ord("c"):
            here = os.path.dirname(os.path.abspath(__file__))
            cv2.imwrite(os.path.join(here, "capture_frame.png"), raw_frame)
            cv2.imwrite(os.path.join(here, "capture_mask.png"), green_mask)
            print("Saved capture_frame.png and capture_mask.png")
        elif key in (ord("-"), ord("=")):
            if exposure is None:                                  # first press: take over from auto
                cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)         # 0.25 = manual on Windows cameras
                exposure = cap.get(cv2.CAP_PROP_EXPOSURE)
            exposure += -1 if key == ord("-") else 1
            cap.set(cv2.CAP_PROP_EXPOSURE, exposure)
            print(f"Exposure set to {exposure} (more negative = darker)")

        # stop cleanly if the window was closed with the X button
        try:
            if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break
        except cv2.error:
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
