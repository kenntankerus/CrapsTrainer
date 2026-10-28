"""
A pretend camera for trying the web app without one (CRAPS_CAMERA=demo) and for the tests.
It draws two green dice on a red carpet: they tumble for a moment, land, and sit still,
then the next throw comes. dice_vision.py reads the pictures exactly as it reads a real camera's.
"""

import random
import time

import cv2
import numpy as np

PIPS = {1: [(.5, .5)], 2: [(.25, .25), (.75, .75)], 3: [(.25, .25), (.5, .5), (.75, .75)],
        4: [(.25, .25), (.75, .25), (.25, .75), (.75, .75)],
        5: [(.25, .25), (.75, .25), (.5, .5), (.25, .75), (.75, .75)],
        6: [(.25, .25), (.25, .5), (.25, .75), (.75, .25), (.75, .5), (.75, .75)]}
SIZE = 60            # a die's side in a 1920-wide picture, about what the Brio sees from above the table


def draw_die(frame, value, cx, cy, angle):
    die = np.zeros((SIZE, SIZE, 3), np.uint8)
    die[:] = (60, 170, 40)
    for x, y in PIPS[value]:
        cv2.circle(die, (int(x * SIZE), int(y * SIZE)), 6, (245, 245, 245), -1)
    h, w = frame.shape[:2]
    layer, mask = np.zeros_like(frame), np.zeros((h, w), np.uint8)
    x0, y0 = int(cx - SIZE / 2), int(cy - SIZE / 2)
    layer[y0:y0 + SIZE, x0:x0 + SIZE] = die
    mask[y0:y0 + SIZE, x0:x0 + SIZE] = 255
    turn = cv2.getRotationMatrix2D((float(cx), float(cy)), angle, 1)
    layer, mask = cv2.warpAffine(layer, turn, (w, h)), cv2.warpAffine(mask, turn, (w, h))
    frame[mask > 128] = layer[mask > 128]


class DemoCapture:
    """Behaves like cv2.VideoCapture: read() gives the next picture, release() closes it."""

    def __init__(self, throws=None, fps=15, tumble=0.8, rest=4.0, width=1920, height=1080, seed=None):
        self.throws = list(throws) if throws else None      # (left, right) pairs to throw in order; random without
        self.fps, self.tumble, self.rest = fps, tumble, rest
        self.width, self.height = width, height
        self.random = random.Random(seed)
        self.carpet = np.full((height, width, 3), (40, 30, 90), np.uint8)
        self.started = time.monotonic()
        self.thrown = 0
        self.landed = None
        self.last = 0.0

    def _next_landing(self):
        if self.throws is not None:
            dice = self.throws[self.thrown % len(self.throws)]
        else:
            dice = (self.random.randint(1, 6), self.random.randint(1, 6))
        r = self.random
        left = (r.randint(500, 850), r.randint(350, 700), r.randint(0, 89))
        right = (r.randint(1050, 1400), r.randint(350, 700), r.randint(0, 89))
        return dice, left, right

    def read(self):
        wait = 1 / self.fps - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()
        cycle = self.tumble + self.rest
        elapsed = self.last - self.started
        number, into = int(elapsed // cycle), elapsed % cycle
        frame = self.carpet.copy()
        if number >= self.thrown or self.landed is None:
            self.landed = self._next_landing()
            self.thrown = number + 1
        dice, left, right = self.landed
        if into < self.tumble:                  # rolling across the carpet
            r = self.random
            for _ in range(2):
                draw_die(frame, r.randint(1, 6), r.randint(300, 1600), r.randint(250, 800), r.randint(0, 89))
        else:
            draw_die(frame, dice[0], *left)
            draw_die(frame, dice[1], *right)
        return True, frame

    def isOpened(self):
        return True

    def release(self):
        pass
