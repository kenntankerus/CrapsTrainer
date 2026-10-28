"""
The camera on the server, with the pretend camera from web/camera_demo.py standing in for a webcam.
Needs opencv-python-headless and numpy as well as the web server's requirements.
"""

import asyncio
import json
import os
import threading
import time

import pytest

from test_web import me, signed_in_client

cv2 = pytest.importorskip("cv2")

from web import camera, camera_demo, config, db, events  # noqa: E402

EMAIL = "camera@example.com"


@pytest.fixture
def demo_camera(monkeypatch):
    monkeypatch.setattr(config, "CAMERA_SOURCE", "demo")
    monkeypatch.setattr(config, "CAMERA_USERS", {EMAIL})
    throws = [(3, 4), (6, 6), (2, 5)]
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
