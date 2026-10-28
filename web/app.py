"""
The web server. Run it with:   python -m web          (see web/README.md for the whole setup)

Pages come from web/static; everything the page does goes through /api. Each signed-in account
has a table with its own player plus any guests, and each player has a bankroll, bets and a throw log.
"""

import asyncio
import csv
import io
import json
import logging
import os
import re
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, Response as RawResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config

sys.path.insert(0, config.ROOT)       # craps_engine.py and friends live at the top of the repo

from craps_engine import HOP_COMBOS, BetError, money  # noqa: E402
from dice_stats import COMPARE_MODES, HARDWAY_SETUPS  # noqa: E402
from stickman import DEFAULT_VOICE, VOICES, pack_name  # noqa: E402

from . import auth, camera, db, events, mail, stats, tables, voice  # noqa: E402

log = logging.getLogger("craps")
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
BUY_INS = (10000, 20000, 50000, 100000)
MAX_BUY_IN = 100_000_000                  # $1,000,000
NAME_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,30}$")
FIELDS = ["timestamp", "session", "dice_set", "die1", "die2", "total", "back_wall", "source", "position",
          "camera_read", "throw_id", "user"]        # dice_throws.csv's columns, so an export opens in the tracker


@asynccontextmanager
async def lifespan(app):
    db.init()
    events.bind(asyncio.get_running_loop())
    camera.setup()
    if not config.SMTP_HOST:
        log.warning("CRAPS_SMTP_HOST isn't set: sign-in links are written to this log instead of being emailed.")
    if config.CAMERA_SOURCE and camera.dv is None:
        log.warning("CRAPS_CAMERA is set but the camera can't run: %s", camera.IMPORT_ERROR)
    yield
    camera.service.stop()


app = FastAPI(title=config.SITE_NAME, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

CSP = ("default-src 'self'; script-src 'self' https://challenges.cloudflare.com; "
       "frame-src https://challenges.cloudflare.com; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "media-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


class Guard:
    """Refuses changes without the page's own header (a page on another site can't add it, so it can't act
    with the visitor's cookie), and adds the security headers to every response. Plain ASGI rather than
    @app.middleware, which would hold up the live event stream."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        if path.startswith("/api/") and scope["method"] not in ("GET", "HEAD") \
                and headers.get("x-requested-with") != "craps":
            return await JSONResponse({"error": "Missing request header."}, status_code=403)(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                have = {k.decode().lower() for k, _ in message.get("headers", [])}
                extra = [("content-security-policy", CSP), ("x-content-type-options", "nosniff"),
                         ("referrer-policy", "no-referrer"),       # sign-in links carry their token in the address
                         ("x-frame-options", "DENY")]
                if path.startswith("/api/"):
                    extra.append(("cache-control", "no-store"))
                message["headers"] = list(message.get("headers", [])) + [
                    (k.encode(), v.encode()) for k, v in extra if k not in have]
            await send(message)

        await self.app(scope, receive, send_with_headers)


app.add_middleware(Guard)


@app.exception_handler(BetError)
async def bet_refused(request, error):
    return JSONResponse({"error": str(error)}, status_code=409)


@app.exception_handler(tables.TableError)
async def table_error(request, error):
    return JSONResponse({"error": str(error)}, status_code=404)


@app.exception_handler(auth.AuthError)
async def auth_error(request, error):
    return JSONResponse({"error": str(error)}, status_code=400)


@app.exception_handler(HTTPException)
async def http_error(request, error):
    return JSONResponse({"error": error.detail}, status_code=error.status_code)


def client_ip(request: Request):
    if config.TRUST_PROXY:
        forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def signed_in(request: Request):
    user = auth.session_user(request.cookies.get(auth.COOKIE))
    if user is None:
        raise HTTPException(401, "Sign in first.")
    return user


def settings_for(user):
    return db.settings_of(user)


def buy_in_of(user):
    value = settings_for(user).get("buy_in")
    return value if isinstance(value, int) and 0 < value <= MAX_BUY_IN else tables.DEFAULT_BUY_IN


def premium(user):
    return user["email"] in config.PREMIUM_USERS


def personal_of(user):
    return settings_for(user).get("personal", True) is not False


def voice_of(user):
    value = settings_for(user).get("voice", DEFAULT_VOICE)
    return value if value in VOICES else "Off"


def check_name(name):
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise HTTPException(400, "Names are 1 to 30 characters.")
    return name


def client_of(request: Request):
    """Each open page names itself, so it can skip the news of its own changes."""
    return (request.headers.get("x-client-id") or "")[:40]


def changed(user, request, player_id=None, kind="changed"):
    events.publish(user["id"], kind, player_id=player_id, client=client_of(request))


def check_cents(cents):
    if not isinstance(cents, int) or cents <= 0 or cents % 100 or cents > MAX_BUY_IN:
        raise HTTPException(400, "Buy-ins are whole dollars, up to $1,000,000.")
    return cents


# ---------------------------------------------------------------------- pages
def page(name):
    response = FileResponse(os.path.join(STATIC, name))
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
@app.get("/auth/verify", include_in_schema=False)
def index():
    return page("index.html")


@app.get("/privacy", include_in_schema=False)
def privacy():
    return page("privacy.html")


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    return FileResponse(os.path.join(STATIC, "manifest.webmanifest"), media_type="application/manifest+json")


@app.get("/api/config")
def site_config():
    return {
        "site": config.SITE_NAME, "turnstile": config.TURNSTILE_SITE_KEY,
        "voices": [{"name": v, "slug": pack_name(v)} for v in VOICES], "default_voice": DEFAULT_VOICE,
        "setups": HARDWAY_SETUPS, "modes": COMPARE_MODES, "chips": tables.CHIPS, "hops": HOP_COMBOS,
        "buy_ins": [{"cents": c, "label": money(c)} for c in BUY_INS],
    }


# ---------------------------------------------------------------------- signing in
class LinkRequest(BaseModel):
    email: str = Field(max_length=300)
    turnstile: str = Field("", max_length=4096)


@app.post("/api/auth/request")
def request_link(body: LinkRequest, request: Request):
    email = auth.clean_email(body.email)
    ip = client_ip(request)
    if not auth.check_turnstile(body.turnstile, ip):
        raise auth.AuthError("The bot check didn't go through. Try again.")
    token = auth.new_link(email, ip)
    mail.sign_in_email(email, auth.link_url(token), config.LINK_MINUTES)
    return {"ok": True, "message": f"A sign-in link is on its way to {email}. It works for {config.LINK_MINUTES} minutes."}


@app.get("/api/auth/link")
def link_info(token: str = ""):
    info = auth.peek_link(token)
    if info is None:
        raise auth.AuthError("That link has expired or was already used. Ask for a new one.")
    return info


class Token(BaseModel):
    token: str = Field(max_length=200)


@app.post("/api/auth/verify")
def verify(body: Token, request: Request, response: Response):
    _, session, claimed = auth.use_link(body.token, request.headers.get("user-agent", ""))
    response.set_cookie(auth.COOKIE, session, max_age=config.SESSION_DAYS * 86400, httponly=True,
                        secure=config.SECURE_COOKIES, samesite="lax", path="/")
    return {"ok": True, "claimed": claimed}


class SignOut(BaseModel):
    everywhere: bool = False


@app.post("/api/auth/signout")
def sign_out(body: SignOut, request: Request, response: Response):
    token = request.cookies.get(auth.COOKIE)
    user = auth.session_user(token)
    auth.end_session(token, user["id"] if user is not None and body.everywhere else None)
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


# ---------------------------------------------------------------------- the account
def players_of(user_id):
    rows = db.all_rows("SELECT p.id, p.name, p.is_self, (SELECT COUNT(*) FROM throws t WHERE t.player_id = p.id) AS n "
                       "FROM players p WHERE p.owner_id = ? ORDER BY p.is_self DESC, p.id", user_id)
    return [{"id": r["id"], "name": r["name"], "is_self": bool(r["is_self"]), "throws": r["n"]} for r in rows]


@app.get("/api/me")
def me(user=Depends(signed_in)):
    with db.transaction() as conn:
        auth.self_player(conn, user["id"])
    players = players_of(user["id"])
    settings = settings_for(user)
    if settings.get("player_id") not in [p["id"] for p in players]:
        settings["player_id"] = players[0]["id"]
    throws = []
    for p in players:
        throws += tables.player_throws(db.connect(), p["id"])
    sets, positions = stats.known_names(throws)
    return {"email": user["email"], "name": user["name"], "settings": settings, "players": players,
            "sets": sets, "positions": positions, "max_guests": config.MAX_GUESTS,
            "camera": camera.service.status(user)}


class Settings(BaseModel):
    player_id: Optional[int] = None
    voice: Optional[str] = None
    chip: Optional[int] = None
    hop: Optional[str] = None
    buy_in: Optional[int] = None
    sounds: Optional[bool] = None
    personal: Optional[bool] = None
    back_wall: Optional[bool] = None
    dice_set: Optional[str] = Field(None, max_length=40)
    setup: Optional[str] = None
    position: Optional[str] = Field(None, max_length=40)
    compare: Optional[str] = None
    tab: Optional[str] = Field(None, max_length=20)


class MeUpdate(BaseModel):
    name: Optional[str] = None
    settings: Optional[Settings] = None


@app.post("/api/me")
def update_me(body: MeUpdate, request: Request, user=Depends(signed_in)):
    with db.transaction() as conn:
        if body.name is not None:
            name = check_name(body.name)
            conn.execute("UPDATE users SET name = ? WHERE id = ?", (name, user["id"]))
            conn.execute("UPDATE players SET name = ? WHERE owner_id = ? AND is_self = 1", (name, user["id"]))
        if body.settings is not None:
            current = db.settings_of(conn.execute("SELECT settings FROM users WHERE id = ?", (user["id"],)).fetchone())
            new = body.settings.model_dump(exclude_none=True)
            if "voice" in new and new["voice"] not in VOICES and new["voice"] != "Off":
                del new["voice"]
            if "chip" in new and new["chip"] not in tables.CHIPS:
                del new["chip"]
            if "hop" in new and new["hop"] not in HOP_COMBOS:
                del new["hop"]
            if "buy_in" in new:
                check_cents(new["buy_in"])
            if "setup" in new and new["setup"] not in HARDWAY_SETUPS + [""]:
                del new["setup"]
            if "compare" in new and new["compare"] not in COMPARE_MODES:
                del new["compare"]
            if "player_id" in new:
                tables.player_row(conn, user["id"], new["player_id"])
            current.update(new)
            conn.execute("UPDATE users SET settings = ? WHERE id = ?", (json.dumps(current), user["id"]))
    if body.name is not None:
        changed(user, request, kind="players")
    return {"ok": True}


class Confirm(BaseModel):
    confirm: str = ""


@app.post("/api/me/delete")
def delete_me(body: Confirm, response: Response, user=Depends(signed_in)):
    if body.confirm != "DELETE":
        raise HTTPException(400, "Type DELETE to confirm.")
    throw_ids = [r["throw_id"] for r in db.all_rows(
        "SELECT t.throw_id FROM throws t JOIN players p ON p.id = t.player_id WHERE p.owner_id = ?", user["id"])]
    if camera.service.owner and camera.service.owner["user_id"] == user["id"]:
        camera.service.stop()
    with db.transaction() as conn:
        conn.execute("DELETE FROM links WHERE email = ?", (user["email"],))
        conn.execute("DELETE FROM users WHERE id = ?", (user["id"],))
    for throw_id in throw_ids:
        delete_crops(throw_id)
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@app.get("/api/export.csv")
def export(user=Depends(signed_in)):
    rows = db.all_rows("SELECT t.*, p.name AS user FROM throws t JOIN players p ON p.id = t.player_id "
                       "WHERE p.owner_id = ? ORDER BY t.timestamp, t.id", user["id"])
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=FIELDS, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({**dict(r), "back_wall": str(bool(r["back_wall"]))})
    return StreamingResponse(iter([out.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="dice_throws.csv"'})


# ---------------------------------------------------------------------- players at the table
class PlayerName(BaseModel):
    name: str = Field(max_length=100)


@app.post("/api/players")
def add_guest(body: PlayerName, request: Request, user=Depends(signed_in)):
    name = check_name(body.name)
    with db.transaction() as conn:
        guests = conn.execute("SELECT COUNT(*) FROM players WHERE owner_id = ? AND is_self = 0", (user["id"],)).fetchone()[0]
        if guests >= config.MAX_GUESTS:
            raise HTTPException(400, f"A table seats {config.MAX_GUESTS} guests at most.")
        player_id = conn.execute("INSERT INTO players (owner_id, is_self, name, created) VALUES (?, 0, ?, ?)",
                                 (user["id"], name, datetime.now().isoformat(timespec="seconds"))).lastrowid
    changed(user, request, kind="players")
    return {"id": player_id}


@app.post("/api/players/{player_id}")
def rename_player(player_id: int, body: PlayerName, request: Request, user=Depends(signed_in)):
    name = check_name(body.name)
    with db.transaction() as conn:
        row = tables.player_row(conn, user["id"], player_id)
        conn.execute("UPDATE players SET name = ? WHERE id = ?", (name, player_id))
        if row["is_self"]:
            conn.execute("UPDATE users SET name = ? WHERE id = ?", (name, user["id"]))
    changed(user, request, kind="players")
    return {"ok": True}


@app.post("/api/players/{player_id}/delete")
def delete_player(player_id: int, request: Request, user=Depends(signed_in)):
    with db.transaction() as conn:
        row = tables.player_row(conn, user["id"], player_id)
        if row["is_self"]:
            raise HTTPException(400, "Your own seat stays. Delete the account instead.")
        throw_ids = [r["throw_id"] for r in conn.execute("SELECT throw_id FROM throws WHERE player_id = ?", (player_id,))]
        conn.execute("DELETE FROM players WHERE id = ?", (player_id,))
    for throw_id in throw_ids:
        delete_crops(throw_id)
    changed(user, request, kind="players")
    return {"ok": True}


class Invite(BaseModel):
    email: str = Field(max_length=300)


@app.post("/api/players/{player_id}/invite")
def invite_guest(player_id: int, body: Invite, request: Request, user=Depends(signed_in)):
    email = auth.clean_email(body.email)
    row = tables.player_row(db.connect(), user["id"], player_id)
    if row["is_self"]:
        raise HTTPException(400, "That's your own seat.")
    if email == user["email"]:
        raise HTTPException(400, "That's your own address. Send it to the guest's email.")
    token = auth.new_link(email, client_ip(request), "claim", player_id)
    mail.claim_email(email, auth.link_url(token), user["name"] or user["email"], row["name"], config.LINK_MINUTES)
    return {"ok": True, "message": f"Sent {row['name']} a link at {email}. Their throws move over when they open it."}


# ---------------------------------------------------------------------- the craps table
@app.get("/api/players/{player_id}/table")
def table_view(player_id: int, user=Depends(signed_in)):
    return tables.view(user["id"], player_id, buy_in_of(user))


class Change(BaseModel):
    action: str = Field(max_length=20)
    zone: str = Field("", max_length=20)
    to: str = Field("", max_length=20)
    hop: str = Field("", max_length=5)
    cents: int = 0
    on: bool = False


@app.post("/api/players/{player_id}/change")
def change(player_id: int, body: Change, request: Request, user=Depends(signed_in)):
    if body.action in ("rebuy", "reset"):
        check_cents(body.cents)
    message = tables.change(user["id"], player_id, body.action, buy_in_of(user), zone=body.zone, to=body.to,
                            hop=body.hop, cents=body.cents, on=body.on)
    changed(user, request, player_id)
    return {"message": message, "table": tables.view(user["id"], player_id, buy_in_of(user))}


class Dice(BaseModel):
    die1: int = Field(ge=1, le=6)
    die2: int = Field(ge=1, le=6)
    back_wall: bool = True
    dice_set: str = Field("", max_length=50)
    position: str = Field("", max_length=40)
    local_time: str = Field("", max_length=19)
    mode: str = "table"                  # "table" settles bets, "tracker" only logs the throw


@app.post("/api/players/{player_id}/roll")
def roll(player_id: int, body: Dice, request: Request, user=Depends(signed_in)):
    meta = {"dice_set": body.dice_set.strip(), "position": body.position.strip(), "local_time": body.local_time}
    camera_read, crops = camera.service.take_unclear(user["id"], player_id)     # dice the camera just couldn't read
    if body.mode == "tracker":
        said, throw_id = tables.log_throw(user["id"], player_id, body.die1, body.die2, body.back_wall, meta,
                                          voice_of(user), camera_read=camera_read)
        message = f"Logged {body.die1}-{body.die2} on {meta['dice_set'] or 'Unnamed'}"
    else:
        said, throw_id = tables.roll(user["id"], player_id, body.die1, body.die2, body.back_wall, meta,
                                     voice_of(user), buy_in_of(user), camera_read=camera_read,
                                     premium=premium(user), personal=personal_of(user))
        message = ""
    camera.save_crops(throw_id, crops)
    changed(user, request, player_id)
    return {"message": message, **said}


@app.post("/api/players/{player_id}/fix")
def fix(player_id: int, body: Dice, request: Request, user=Depends(signed_in)):
    message, said = tables.fix_last(user["id"], player_id, body.die1, body.die2, voice_of(user), premium(user),
                                    personal_of(user))
    changed(user, request, player_id)
    return {"message": message, **said}


@app.post("/api/players/{player_id}/undo")
def undo(player_id: int, request: Request, user=Depends(signed_in)):
    message, throw_id = tables.undo(user["id"], player_id)
    delete_crops(throw_id)
    changed(user, request, player_id)
    return {"message": message}


@app.get("/api/players/{player_id}/tracker")
def tracker(player_id: int, mode: str = "", dice_set: str = "", position: str = "", user=Depends(signed_in)):
    conn = db.connect()
    tables.player_row(conn, user["id"], player_id)
    return stats.tracker_view(tables.player_throws(conn, player_id), mode, dice_set, position)


# ---------------------------------------------------------------------- live updates
@app.get("/api/events")
def live(user=Depends(signed_in)):
    queue = events.listen(user["id"])
    return StreamingResponse(events.stream(user["id"], queue), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------- the camera
def camera_user(user=Depends(signed_in)):
    if not camera.allowed(user):
        raise HTTPException(403, "This account can't use the camera.")
    return user


class CameraBinding(BaseModel):
    player_id: int
    mode: str = "table"
    back_wall: bool = True
    dice_set: str = Field("", max_length=50)
    position: str = Field("", max_length=40)
    tz_offset: int = 0


def binding_of(body, user):
    tables.player_row(db.connect(), user["id"], body.player_id)
    return {"player_id": body.player_id, "mode": "tracker" if body.mode == "tracker" else "table",
            "back_wall": body.back_wall, "dice_set": body.dice_set.strip(), "position": body.position.strip(),
            "tz_offset": body.tz_offset, "voice": voice_of(user), "buy_in": buy_in_of(user),
            "premium": premium(user), "personal": personal_of(user)}


@app.get("/api/camera")
def camera_status(user=Depends(camera_user)):
    return camera.service.status(user)


@app.post("/api/camera/start")
def camera_start(body: CameraBinding, request: Request, user=Depends(camera_user)):
    try:
        camera.service.start(user, client_of(request), binding_of(body, user))
    except camera.CameraError as error:
        raise HTTPException(409, str(error))
    changed(user, request, kind="camera")
    return camera.service.status(user)


@app.post("/api/camera/update")
def camera_update(body: CameraBinding, request: Request, user=Depends(camera_user)):
    try:
        camera.service.update(user, client_of(request), binding_of(body, user))
    except camera.CameraError as error:
        raise HTTPException(409, str(error))
    return camera.service.status(user)


@app.post("/api/camera/stop")
def camera_stop(request: Request, user=Depends(camera_user)):
    try:
        camera.service.stop(user)
    except camera.CameraError as error:
        raise HTTPException(409, str(error))
    return camera.service.status(user)


@app.get("/api/camera/preview.jpg")
def camera_preview(user=Depends(camera_user)):
    jpeg = camera.service.preview()
    if jpeg is None:
        raise HTTPException(404, "No picture yet.")
    return RawResponse(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/camera/settings")
def camera_settings(user=Depends(camera_user)):
    return camera.service.settings()


class Tuning(BaseModel):
    values: dict = {}
    view: Optional[str] = None
    save: bool = False


@app.post("/api/camera/settings")
def camera_tune(body: Tuning, user=Depends(camera_user)):
    try:
        return camera.service.tune(body.values, body.view, body.save)
    except camera.CameraError as error:
        raise HTTPException(400, str(error))


@app.post("/api/camera/train")
def camera_train(user=Depends(camera_user)):
    from .train import train
    made, report = train()
    return {"ok": made, "report": report}


# ---------------------------------------------------------------------- the stickman's voice
@app.get("/api/voice/{slug}/clips")
def voice_clips(slug: str, user=Depends(signed_in)):
    """Every fixed line of a voice, for the page to fetch ahead of time."""
    name = voice.SLUGS.get(slug)
    if name is None:
        raise HTTPException(404, "No such voice.")
    return {"urls": [f"/api/voice/{slug}/{clip}" for clip in voice.clip_names(name)], "engines": voice.engines(name)}


@app.get("/api/voice/{slug}/line/{token}")
async def voice_line(slug: str, token: str, user=Depends(signed_in)):
    path = await voice.line_path(slug, token)
    if path is None:
        raise HTTPException(404, "No recording of that line.")
    return FileResponse(path, media_type=voice.media_type(path), headers={"Cache-Control": "private, max-age=604800"})


@app.get("/api/voice/{slug}/{clip}")
async def voice_clip(slug: str, clip: str, user=Depends(signed_in)):
    path = await voice.clip_path(slug, clip.removesuffix(".mp3"))
    if path is None:
        raise HTTPException(404, "No recording of that line.")
    return FileResponse(path, media_type=voice.media_type(path), headers={"Cache-Control": "private, max-age=604800"})


from .make_sounds import SOUNDS  # noqa: E402


@app.get("/api/sounds")
def sounds(user=Depends(signed_in)):
    """The recorded room and crowd sounds there are (made by python -m web.make_sounds)."""
    return {"sounds": [n for n in SOUNDS if os.path.isfile(os.path.join(config.SOUND_DIR, n + ".mp3"))]}


@app.get("/api/sounds/{name}")
def sound(name: str, user=Depends(signed_in)):
    path = os.path.join(config.SOUND_DIR, name + ".mp3")
    if name not in SOUNDS or not os.path.isfile(path):
        raise HTTPException(404, "No such sound.")
    return FileResponse(path, media_type="audio/mpeg", headers={"Cache-Control": "private, max-age=86400"})


# ---------------------------------------------------------------------- dice pictures
def delete_crops(throw_id):
    if not throw_id or not re.match(r"^[0-9-]+$", throw_id):
        return
    for side in ("left", "right"):
        try:
            os.remove(os.path.join(config.CROP_DIR, f"{throw_id}_{side}.png"))
        except OSError:
            pass
