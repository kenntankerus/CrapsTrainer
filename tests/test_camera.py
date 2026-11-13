"""
The camera on the server, with the pretend camera from web/camera_demo.py standing in for a webcam.
Needs opencv-python-headless and numpy as well as the web server's requirements.
"""

import asyncio
import json
import os
import threading
import time

import numpy as np
import pytest

from test_web import me, signed_in_client

cv2 = pytest.importorskip("cv2")

from web import camera, camera_demo, config, db, events  # noqa: E402

EMAIL = "camera@example.com"


@pytest.fixture
def demo_camera(monkeypatch):
    monkeypatch.setattr(config, "CAMERA_SOURCE", "demo")
    monkeypatch.setattr(config, "CAMERA_USERS", {EMAIL})
    # the pretend camera starts with its first dice already down: that's not a throw, so (1, 5) is never logged
    throws = [(1, 5), (3, 4), (6, 6), (2, 5)]
    monkeypatch.setattr(camera.CameraService, "_source",
                        lambda self: (None, camera_demo.DemoCapture(throws, fps=30, tumble=0.4, rest=2.5, seed=1)))
    yield throws
    camera.service.stop()


def wait_for(check, seconds=20):
    end = time.time() + seconds
    while time.time() < end:
        value = check()
        if value:
            return value
        time.sleep(0.2)
    raise AssertionError("timed out")


def test_demo_capture_reads_cleanly():
    import dice_vision as dv
    capture = camera_demo.DemoCapture([(5, 2)], fps=1000, tumble=0.0, rest=10)
    _, frame = capture.read()
    assert [d["value"] for d in dv.find_dice(frame)] == [5, 2]


def test_only_listed_accounts_get_the_camera(demo_camera):
    c = signed_in_client("not-camera@example.com")
    assert me(c)["camera"]["allowed"] is False
    pid = me(c)["players"][0]["id"]
    assert c.post("/api/camera/start", json={"player_id": pid}).status_code == 403
    assert c.get("/api/camera/preview.jpg").status_code == 403


def test_camera_rolls_the_table(demo_camera):
    c = signed_in_client(EMAIL)
    info = me(c)
    assert info["camera"]["allowed"] is True
    pid = info["players"][0]["id"]
    c.post(f"/api/players/{pid}/change", json={"action": "bet", "zone": "pass", "cents": 500})
    r = c.post("/api/camera/start", json={"player_id": pid, "dice_set": "3-V", "position": "Stick left",
                                          "tz_offset": -300}, headers={"X-Client-Id": "tab1"})
    assert r.status_code == 200 and r.json()["running"] and r.json()["mine"]

    rows = wait_for(lambda: db.all_rows("SELECT * FROM throws WHERE player_id = ? ORDER BY id", pid))
    first = rows[0]
    assert all((r["die1"], r["die2"]) != (1, 5) for r in rows)        # left on the table before the camera started
    assert (first["die1"], first["die2"], first["source"], first["camera_read"]) == (3, 4, "camera", "3-4")
    assert first["dice_set"] == "3-V" and first["position"] == "Stick left"
    view = c.get(f"/api/players/{pid}/table").json()
    assert view["last_roll"]["total"] == 7 and view["bankroll"] == 10000      # the pass line won and stays up
    for side in ("left", "right"):
        assert os.path.exists(os.path.join(config.CROP_DIR, f"{first['throw_id']}_{side}.png"))
    assert c.get("/api/camera/preview.jpg").content[:2] == b"\xff\xd8"

    # switch the camera to the tracker: the next throw is logged without settling bets
    c.post("/api/camera/update", json={"player_id": pid, "mode": "tracker"}, headers={"X-Client-Id": "tab1"})
    rows = wait_for(lambda: (lambda r: r if len(r) >= 2 else None)(
        db.all_rows("SELECT * FROM throws WHERE player_id = ? ORDER BY id", pid)), seconds=25)
    assert rows[1]["table_before"] is None and rows[1]["source"] == "camera"

    # someone else can't take it over or stop it
    other = signed_in_client("camera2@example.com")
    config.CAMERA_USERS.add("camera2@example.com")
    opid = me(other)["players"][0]["id"]
    r = other.post("/api/camera/start", json={"player_id": opid})
    assert r.status_code == 409 and "using the camera" in r.json()["error"]
    assert c.post("/api/camera/stop").json()["running"] is False


def test_tuning_changes_and_saves(demo_camera, tmp_path, monkeypatch):
    monkeypatch.setattr(camera, "SETTINGS_FILE", str(tmp_path / "dice_settings.json"))
    c = signed_in_client(EMAIL)
    before = c.get("/api/camera/settings").json()
    r = c.post("/api/camera/settings", json={"values": {"white_level": 999, "sat_low": 20}, "save": True})
    assert r.status_code == 200 and r.json()["white_level"] == 255 and r.json()["sat_low"] == 20
    saved = json.load(open(tmp_path / "dice_settings.json"))
    assert saved["white_level"] == 255
    c.post("/api/camera/settings", json={"values": {"white_level": before["white_level"], "sat_low": before["sat_low"]}})


def test_unclear_read_goes_with_typed_dice(demo_camera):
    c = signed_in_client(EMAIL)
    pid = me(c)["players"][0]["id"]
    user_id = db.one("SELECT id FROM users WHERE email = ?", EMAIL)["id"]
    camera.service.pending[(user_id, pid)] = (time.time(), [b"left", b"right"])
    c.post(f"/api/players/{pid}/roll", json={"die1": 1, "die2": 3, "mode": "tracker"})
    row = db.one("SELECT * FROM throws WHERE player_id = ? ORDER BY id DESC", pid)
    assert row["camera_read"] == "unclear"
    assert open(os.path.join(config.CROP_DIR, f"{row['throw_id']}_left.png"), "rb").read() == b"left"
    assert c.get(f"/api/players/{pid}/tracker").json()["recent"][0]["tag"] == "typed, cam couldn't read"


def test_events_reach_listeners_from_other_threads():
    async def main():
        events.bind(asyncio.get_running_loop())
        queue = events.listen(42)
        threading.Thread(target=events.publish, args=(42, "changed"), kwargs={"player_id": 7}).start()
        message = json.loads(await asyncio.wait_for(queue.get(), 5))
        events.forget(42, queue)
        return message
    assert asyncio.run(main()) == {"type": "changed", "player_id": 7}


def test_retrain_needs_enough_pictures(demo_camera):
    c = signed_in_client(EMAIL)
    r = c.post("/api/camera/train").json()
    assert r["ok"] is False and "each face needs at least" in r["report"]


def test_camera_on_its_side(monkeypatch):
    # Turned 90 degrees on its mount the camera sends a tall picture; ROTATE turns it back so the shooter's
    # left is the picture's left, and the dice are the same size either way up
    import dice_vision as dv
    _, frame = camera_demo.DemoCapture([(5, 2)], fps=1000, tumble=0.0, rest=10).read()
    sideways = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    assert len(dv.find_dice(sideways)) == 2
    monkeypatch.setattr(dv, "ROTATE", 90)
    turned = dv.turn(sideways)
    assert (turned == frame).all()
    assert [d["value"] for d in dv.find_dice(turned)] == [5, 2]
    camera.service.tune({"rotate": 100})
    assert dv.ROTATE == 90


def test_die_washed_out_by_glare(monkeypatch):
    # A light above the table can bleach a die's green to beige; with the felt's colour given, a die-sized spot
    # ringed by felt is still a die. A light spot on the black laptop at the near end isn't ringed by felt,
    # and spots of sun on the felt are still felt.
    import dice_vision as dv
    frame = np.full((1080, 1920, 3), (60, 30, 120), np.uint8)              # red felt
    camera_demo.draw_die(frame, 3, 600, 500, 20)
    pale = np.zeros_like(frame)
    camera_demo.draw_die(pale, 5, 1100, 500, 10)
    die = pale.any(axis=2)
    frame[die] = pale[die]
    body = die & (pale[..., 0] < 200)                                       # the pips stay white
    frame[body] = (0.1 * pale[body] + 0.9 * np.array([170, 205, 230])).astype(np.uint8)   # glare
    sun = cv2.cvtColor(np.uint8([[[154, 70, 250]]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()
    for x in range(700, 1500, 100):                                        # sun through a shade: a row of spots
        cv2.ellipse(frame, (x, 800), (42, 32), 0, 0, 360, sun, -1)
    frame[900:1080, 0:500] = (25, 20, 35)                                    # the laptop, touching the edge
    frame[950:1010, 200:260] = (210, 215, 220)                               # a key, light and die-sized
    frame = cv2.GaussianBlur(frame, (9, 9), 0)
    felt_hue = int(cv2.cvtColor(np.uint8([[[60, 30, 120]]]), cv2.COLOR_BGR2HSV)[0, 0, 0])

    monkeypatch.setattr(dv, "FELT_HUE", None)
    assert [d["value"] for d in dv.find_dice(frame)] == [3]
    monkeypatch.setattr(dv, "FELT_HUE", felt_hue)
    assert [d["value"] for d in dv.find_dice(frame)] == [3, 5]


def test_die_seen_at_a_slant(monkeypatch):
    # Far from the spot under the camera a die shows the side facing it too, here three pips below a 4.
    # Trimmed to its top face (NADIR and TILT), only the 4 is counted.
    import dice_vision as dv
    frame = np.full((1080, 1920, 3), (60, 30, 120), np.uint8)
    camera_demo.draw_die(frame, 4, 960, 150, 0)
    frame[180:200, 930:990] = (60, 170, 40)                                # the side face, towards the camera
    for x in (942, 960, 978):
        cv2.circle(frame, (x, 190), 5, (245, 245, 245), -1)
    frame = cv2.GaussianBlur(frame, (5, 5), 0)

    monkeypatch.setattr(dv, "TILT", None)
    assert [d["pips"] for d in dv.find_dice(frame)] == [7]
    monkeypatch.setattr(dv, "NADIR", [960, 1000])
    monkeypatch.setattr(dv, "TILT", 0.024)
    assert [(d["pips"], d["value"]) for d in dv.find_dice(frame)] == [(4, 4)]
    # Trimmed too hard (the side face in shadow shows less than TILT allows), the top loses pips; then the
    # pips farthest from the camera's spot that make a face are taken
    monkeypatch.setattr(dv, "TILT", 0.06)
    assert [(d["pips"], d["value"]) for d in dv.find_dice(frame)] == [(4, 4)]


class SlidingCapture:
    """14 pictures a second, like the Brio on the server: two dice sit still (the starting picture), then
    a new pair slides slowly across the carpet for four seconds, a pixel a picture, and stops."""

    def __init__(self):
        self.started = time.perf_counter()

    def read(self):
        time.sleep(1 / 14)
        t = time.perf_counter() - self.started
        frame = np.zeros((1080, 1920, 3), np.uint8)
        frame[:] = (60, 30, 120)                                     # the carpet
        if t < 3:
            values, x = (1, 2), 600
        else:
            values, x = (5, 6), 600 + 14 * min(t - 3, 4)            # slides from 3 s to 7 s, then sits
        camera_demo.draw_die(frame, values[0], int(x), 500, 10)
        camera_demo.draw_die(frame, values[1], int(x) + 200, 520, 30)
        return True, cv2.GaussianBlur(frame, (9, 9), 0)            # a real lens isn't razor sharp

    def isOpened(self):
        return True

    def release(self):
        pass


def test_slow_slide_is_still_moving():
    # Checked picture to picture, 14 a second is too fast to see a slow slide, and dice still moving got read
    import dice_vision as dv
    cam = dv.DiceCamera(capture=SlidingCapture())
    cam.start()
    try:
        started = time.perf_counter()
        kind, payload = cam.events.get(timeout=20)
        reported_at = time.perf_counter() - started
    finally:
        cam.stop()
    assert kind == "throw" and payload["dice"] == (5, 6)
    assert reported_at > 7, f"reported at {reported_at:.1f} s, while the dice were still sliding"


def test_pictures_from_the_page(monkeypatch):
    # The page opens its own camera and sends its pictures; the server reads them as it reads its own webcam's
    monkeypatch.setattr(config, "CAMERA_SOURCE", "")            # no webcam on the server: not needed for this
    monkeypatch.setattr(config, "CAMERA_USERS", {EMAIL})
    monkeypatch.setattr(camera, "PAGE_WAIT", 1.0)
    c = signed_in_client(EMAIL)
    pid = me(c)["players"][0]["id"]
    page = {"X-Client-Id": "page1"}
    assert c.post("/api/camera/picture", content=b"x", headers=page).status_code == 410      # not started yet
    r = c.post("/api/camera/start", json={"player_id": pid, "mode": "tracker", "from_page": True}, headers=page)
    assert r.status_code == 200 and r.json()["from_page"] is True
    assert c.post("/api/camera/picture", content=b"not a picture", headers=page).status_code == 409
    assert c.post("/api/camera/picture", content=b"x", headers={"X-Client-Id": "page2"}).status_code == 409

    # the first dice are on the table before anyone throws, so (1, 5) is never logged
    capture = camera_demo.DemoCapture([(1, 5), (3, 4), (6, 6)], fps=30, tumble=0.4, rest=2.0, seed=1)
    try:
        before = len(db.all_rows("SELECT * FROM throws WHERE player_id = ?", pid))
        end = time.time() + 15
        seen = set()
        while time.time() < end:
            _, frame = capture.read()
            jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()
            r = c.post("/api/camera/picture", content=jpeg, headers=page)
            assert r.status_code == 200
            # each answer says what the reader found, for the page to draw over its picture
            seen.add(tuple(d["value"] for d in r.json()["dice"]))
            rows = db.all_rows("SELECT * FROM throws WHERE player_id = ? ORDER BY id", pid)[before:]
            if len(rows) >= 2:
                break
        assert [(r["die1"], r["die2"], r["source"]) for r in rows[:2]] == [(3, 4, "camera"), (6, 6, "camera")]
        assert (3, 4) in seen and r.json()["roi"] is None
        # the reader's mask of the newest picture: white where a die could be
        mask = cv2.imdecode(np.frombuffer(c.get("/api/camera/mask.jpg").content, np.uint8), cv2.IMREAD_GRAYSCALE)
        assert mask.shape == (270, 480) and mask.max() > 200
        # the page goes quiet (closed): the reader stops by itself
        wait_for(lambda: not camera.service.status({"email": EMAIL, "id": 0})["running"], 5)
        assert "stopped coming" in camera.service.error
        assert c.post("/api/camera/picture", content=jpeg, headers=page).status_code == 410
    finally:
        camera.service.stop()


def test_page_camera_controls_come_from_the_settings_file(tmp_path, monkeypatch):
    # The page sets its camera's exposure and so on from "page_camera", read afresh so a change needs no restart
    monkeypatch.setattr(config, "CAMERA_USERS", {EMAIL})
    monkeypatch.setattr(camera, "SETTINGS_FILE", str(tmp_path / "dice_settings.json"))
    c = signed_in_client(EMAIL)
    assert c.get("/api/camera/settings").json()["page_camera"] == {}
    (tmp_path / "dice_settings.json").write_text(json.dumps({"page_camera": {"exposureMode": "manual", "exposureTime": 625}}))
    assert c.get("/api/camera/settings").json()["page_camera"] == {"exposureMode": "manual", "exposureTime": 625}


def test_each_camera_keeps_its_own_settings(monkeypatch):
    import dice_vision as dv
    monkeypatch.setattr(config, "CAMERA_SOURCE", "")
    monkeypatch.setattr(config, "CAMERA_USERS", {EMAIL, "camera2@example.com"})
    c = signed_in_client(EMAIL)
    pid = me(c)["players"][0]["id"]
    page = {"X-Client-Id": "page1"}
    default_roi = dv.ROI
    # a camera with nothing saved reads with the defaults
    r = c.get("/api/camera/profile", params={"camera": "Test Cam (1234:5678)"}).json()
    assert r["saved"] is False and r["settings"]["roi"] == default_roi and r["settings"]["use_model"] is True
    # settings are checked
    for bad in ({"roi": [10, 10, 5, 50]}, {"hue_low": 300}, {"nope": 1}, {"page_camera": {"exposureMode": "off"}}):
        assert c.post("/api/camera/profile", json={"camera": "Test Cam (1234:5678)", "settings": bad}).status_code == 400
    r = c.post("/api/camera/profile", json={"camera": "Test Cam (1234:5678)", "settings": {
        "roi": [0, 0, 500, 400], "use_model": False, "page_camera": {"exposureMode": "manual", "exposureTime": 312.5}}}).json()
    assert r["saved"] is True and r["settings"]["roi"] == [0, 0, 500, 400] and r["settings"]["hue_low"] == dv.HUE_LOW
    # they're this account's: another account's same camera still has the defaults
    other = signed_in_client("camera2@example.com")
    assert other.get("/api/camera/profile", params={"camera": "Test Cam (1234:5678)"}).json()["saved"] is False

    # while that camera reads, the reader uses its settings; a change applies at once; stopped, the defaults are back
    try:
        assert c.post("/api/camera/start", json={"player_id": pid, "from_page": True, "camera": "Test Cam (1234:5678)"},
                      headers=page).status_code == 200
        assert dv.ROI == [0, 0, 500, 400] and dv.USE_MODEL is False
        c.post("/api/camera/profile", json={"camera": "Test Cam (1234:5678)", "settings": {"roi": [0, 0, 600, 400]}})
        assert dv.ROI == [0, 0, 600, 400] and dv.USE_MODEL is True        # left out: back to its default
        c.post("/api/camera/profile/reset", json={"camera": "Test Cam (1234:5678)"})
        assert dv.ROI == default_roi
    finally:
        camera.service.stop()
    assert dv.ROI == default_roi and dv.USE_MODEL is True


def _table(felt=(40, 30, 90), laptop=True):
    """A drawn table: felt, wood walls, and a laptop with a lit strip at the near end."""
    frame = np.full((1080, 1920, 3), felt, np.uint8)
    frame[:40, :] = (130, 160, 210)
    frame[:, :30] = frame[:, 1890:] = (130, 160, 210)
    if laptop:
        frame[860:, :520] = (35, 35, 38)
        frame[870:890, 40:480] = (230, 200, 120)
    return frame


def _red_die(frame, value, cx, cy, angle):
    """A red die with white pips (camera_demo's dice are green)."""
    layer = np.zeros_like(frame)
    camera_demo.draw_die(layer, value, cx, cy, angle)
    die = layer.any(axis=2)
    body = die & (layer[..., 0] < 200)
    frame[die] = layer[die]
    frame[body] = (40, 30, 200)


def test_setup_measures_the_table_and_dice():
    from web import camera_profiles, camera_setup
    import dice_vision as dv
    settings = {**camera_profiles.defaults(), "roi": None}
    empty = cv2.GaussianBlur(_table(), (5, 5), 0)
    r = camera_setup.empty_table(empty, settings)
    x0, y0, x1, y1 = r["settings"]["roi"]
    assert y1 <= 860 and x0 <= 30 and x1 >= 1890        # stops above the laptop, takes in the walls
    assert any(s.get("thing") for s in r["left_out"]) and not r["still"]
    draft = {**settings, **r["settings"]}

    # a die left on the "empty" table is said, not cut round
    left = empty.copy()
    camera_demo.draw_die(left, 4, 900, 500, 10)
    with pytest.raises(camera_setup.SetupError, match="looks like a die"):
        camera_setup.empty_table(left, settings)

    dice = empty.copy()
    camera_demo.draw_die(dice, 3, 800, 450, 20)
    camera_demo.draw_die(dice, 5, 1200, 600, 65)
    r = camera_setup.two_dice(dice, draft, empty)
    assert r["read"] and [d["value"] for d in r["dice"]] == [3, 5]
    assert r["settings"]["use_model"] is True          # green, like the dice the model learned

    # red dice on green felt: their hues wrap round through red, and the model is left out
    green = _table(felt=(60, 120, 30), laptop=False)
    r = camera_setup.empty_table(green, settings)
    draft = {**settings, **r["settings"]}
    red = green.copy()
    _red_die(red, 2, 700, 500, 15)
    _red_die(red, 6, 1300, 400, 40)
    r = camera_setup.two_dice(red, draft, green)
    found = r["settings"]
    assert r["read"] and [d["value"] for d in r["dice"]] == [2, 6]
    assert found["hue_low"] > found["hue_high"] and found["use_model"] is False
    with camera_setup.using({**draft, **found}):
        assert [d["value"] for d in dv.find_dice(red)] == [2, 6]
    assert dv.HUE_LOW <= dv.HUE_HIGH                     # put back afterwards


def test_setup_steps_through_the_api(monkeypatch):
    import base64
    monkeypatch.setattr(config, "CAMERA_USERS", {EMAIL})
    c = signed_in_client(EMAIL)
    picture = lambda frame: "data:image/jpeg;base64," + base64.b64encode(cv2.imencode(".jpg", frame)[1].tobytes()).decode()
    empty = _table()
    r = c.post("/api/camera/setup/empty", json={"picture": picture(empty), "settings": {}})
    assert r.status_code == 200, r.text
    draft = r.json()["settings"]
    dice = empty.copy()
    camera_demo.draw_die(dice, 1, 900, 400, 0)
    camera_demo.draw_die(dice, 6, 1300, 500, 30)
    r = c.post("/api/camera/setup/dice", json={"picture": picture(dice), "empty": picture(empty), "settings": draft})
    assert r.status_code == 200 and r.json()["read"], r.text
    assert c.post("/api/camera/setup/dice", json={"picture": picture(empty), "settings": draft}).status_code == 400
    assert c.post("/api/camera/setup/nope", json={"picture": picture(empty)}).status_code == 404
    assert c.post("/api/camera/setup/empty", json={"picture": "not a picture"}).status_code == 400


def test_pictures_are_evened_out_to_the_measured_felt(monkeypatch):
    # A camera the browser can't set changes its own exposure; its pictures are scaled back to the felt measured
    # at setup, so the reader's numbers (pips, colours) still fit
    from web import camera_setup
    table = _table(laptop=False)
    camera_demo.draw_die(table, 3, 800, 450, 20)
    camera_demo.draw_die(table, 5, 1200, 600, 65)
    settings = {"felt_bgr": [40.0, 30.0, 90.0], "felt_level": 90, "roi": None}
    bright = cv2.convertScaleAbs(table, alpha=1.7)
    evened = camera_setup.even(bright, settings)
    felt = np.median(evened[300:400, 300:600].reshape(-1, 3), axis=0)
    assert np.all(np.abs(felt - [40, 30, 90]) <= 4)
    assert camera_setup.even(table, settings) is table            # near enough already: left alone

    # while reading, pictures from a set-up camera are evened, and the page is told how much
    monkeypatch.setattr(config, "CAMERA_SOURCE", "")
    monkeypatch.setattr(config, "CAMERA_USERS", {EMAIL})
    c = signed_in_client(EMAIL)
    pid = me(c)["players"][0]["id"]
    page = {"X-Client-Id": "page1"}
    c.post("/api/camera/profile", json={"camera": "Phone", "settings": settings})
    jpeg = cv2.imencode(".jpg", bright)[1].tobytes()
    try:
        c.post("/api/camera/start", json={"player_id": pid, "from_page": True, "camera": "Phone"}, headers=page)
        answer = c.post("/api/camera/picture", content=jpeg, headers=page).json()
        assert 0.5 < answer["evened"] < 0.7
        wait_for(lambda: camera.service.camera.frame is not None)
        seen = np.median(camera.service.camera.frame[300:400, 300:600].reshape(-1, 3), axis=0)
        assert np.all(np.abs(seen - [40, 30, 90]) <= 6)
    finally:
        camera.service.stop()
    c.post("/api/camera/profile/reset", json={"camera": "Phone"})


def test_setup_takes_a_dark_table():
    # A short exposure leaves red felt dark (in the 20s) while the dice still stand out: the felt is judged against
    # the picture's own brightness, not a fixed level
    from web import camera_profiles, camera_setup
    settings = {**camera_profiles.defaults(), "roi": None}
    rng = np.random.default_rng(1)
    dark = lambda frame: np.clip(frame * 0.24 + rng.normal(0, 3, frame.shape), 0, 255).astype(np.uint8)
    empty = cv2.GaussianBlur(dark(_table(laptop=False).astype(float)), (5, 5), 0)
    r = camera_setup.empty_table(empty, settings)
    assert not r["still"] and r["level"] < 30

    with pytest.raises(camera_setup.SetupError, match="came out at"):
        camera_setup.empty_table(np.zeros((1080, 1920, 3), np.uint8), settings)


def test_setup_reads_dice_on_a_very_dark_red_felt():
    # Red felt so dark it has no blue or green at all (0, 0, 16). Evening the picture out mustn't scale blue and
    # green down to match it (that turned green dice black), and the dice step must look for the felt as it comes
    # out brightened, not as measured
    from web import camera_profiles, camera_setup
    settings = {**camera_profiles.defaults(), "roi": None}
    table = _table(felt=(0, 0, 70), laptop=False)
    empty = (table * 0.23).astype(np.uint8)
    r = camera_setup.empty_table(empty, settings)
    assert r["settings"]["felt_bgr"][:2] == [0.0, 0.0] and r["level"] < 20
    gains = camera_setup.even_gains(empty, r["settings"]["felt_bgr"], 88)
    assert gains[1] >= gains[2] * 0.99                       # green is brightened like red, not cut down
    draft = {**settings, **r["settings"]}
    dice = table.copy()
    camera_demo.draw_die(dice, 3, 800, 450, 20)
    camera_demo.draw_die(dice, 5, 1200, 600, 65)
    r = camera_setup.two_dice((dice * 0.23).astype(np.uint8), draft, empty)
    assert r["read"] and [d["value"] for d in r["dice"]] == [3, 5]


def test_setup_sets_how_dark_the_felt_may_be():
    # Lit from the rim, the felt fades towards the near end; the reader's ringed-by-felt test takes the limit from
    # the darker felt, and a well-lit table keeps the reader's own 60
    from web import camera_profiles, camera_setup
    import dice_vision as dv
    settings = {**camera_profiles.defaults(), "roi": None}
    assert dv.FELT_VAL_LOW == 60
    lit = camera_setup.empty_table(_table(laptop=False), settings)
    assert lit["settings"]["felt_val_low"] == 60
    fade = np.linspace(1.0, 0.45, 1080)[:, None, None]
    faded = camera_setup.empty_table((_table(laptop=False) * fade).astype(np.uint8), settings)
    assert 20 <= faded["settings"]["felt_val_low"] <= 50
    assert camera_profiles.clean({"felt_val_low": 40}) == {"felt_val_low": 40}
    with camera_setup.using({"felt_val_low": 40}):
        assert dv.FELT_VAL_LOW == 40
    assert dv.FELT_VAL_LOW == 60
