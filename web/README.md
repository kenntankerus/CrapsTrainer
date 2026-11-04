# Craps Trainer on the web

The craps table and the throw tracker as a website: sign in with an emailed link, play from a
phone or a laptop, and keep every throw on your own server. It uses the same rules
(`craps_engine.py`), stats (`dice_stats.py`) and stickman (`stickman.py`) as the desktop apps,
which keep working as before.

What's in this first version:

* **Accounts with emailed sign-in links** (no passwords). The first link makes the account. The link can be
  opened on any device: it can sign in the device that asked for it (which shows a code to match) or itself.
* **Guests**: add players to your table who play on your screen with their own bankroll and stats.
  *Send history* emails a guest a link that moves their throws into an account of their own.
* **The table**: every bet from the desktop game. Click or tap to bet, right-click or
  press-and-hold to take down, drag (mouse) or *Move it…* (touch) to move. *Virtual roll* throws random
  dice for playing with bets; those rolls stay out of the tracker and the stats. Undo and Fix last work
  like Ctrl+Z and F2. Rolls settle on the server, so a phone and a laptop show the same table.
* **The tracker**: log throws without bets, and compare dice sets and positions against fair dice,
  with the Small / Tall / All runs and the totals chart.
* **The stickman**: the same lines and voices, plus personal calls about your own roll ("Pay the hard
  eight, forty-five dollars", your name, what a seven-out cost). Lines come from your recorded packs,
  ElevenLabs, a free voice running on the server, or Microsoft's online voices, best first, and the page
  fetches them all ahead of time so calls play the moment the dice land. See
  [The stickman's voice](#the-stickmans-voice).
* **Sound**: a busy-casino room, a crowd that cheers a point made and groans at a seven-out, dice
  clatter and chip clacks, mixed in the browser with a volume for each (the *Sound* button). The room
  and crowd dip while the stickman talks.
* **The camera**: *Start camera* opens the camera of the device the page is on (a phone, a tablet,
  the table's laptop) and shows the table in a panel. For the accounts in `CRAPS_CAMERA_USERS` the page
  sends the picture to the server, which reads the dice and rolls each throw. See [The camera](#the-camera).
* **Live updates**: a roll on your phone shows up on the laptop straight away, and the other way round.
* Download all your throws as `dice_throws.csv`, or delete your account, from the menu.

## Try it on your own computer

```
pip install -r requirements-web.txt
python -m web
```

Open http://localhost:8000. With no email settings the sign-in link is printed in the terminal
instead of sent: copy it into the browser.

To bring in what the desktop apps logged (run from the repo folder, with the server stopped or running):

```
python -m web.import_data you@example.com
```

That adds every row of `dice_throws.csv` to that account (the `kenn` rows are yours, any other user
becomes a guest), copies `dice_crops/`, and brings over `craps_game_state.json`'s bankrolls if the
file is there. Running it again only adds new rows.

Tests: `pip install pytest httpx` then `python -m pytest tests`.

## Putting it online from the old laptop

The plan: Ubuntu Server on the laptop, the app in Docker, and a free **Cloudflare Tunnel** to put
it online. The tunnel connects out from the laptop to Cloudflare, so nothing on your router needs
opening, your home address stays private, and HTTPS is handled for you.

### 1. The laptop

1. Install **Ubuntu Server** (the current LTS release). Choose "Install OpenSSH server" so you can
   work on it from another computer.
2. Keep it running with the lid closed: in `/etc/systemd/logind.conf` set
   `HandleLidSwitch=ignore` and `HandleLidSwitchExternalPower=ignore`, then
   `sudo systemctl restart systemd-logind`.
3. If its firmware (BIOS/UEFI) has "power on when AC is restored", turn it on, so it comes back
   after a power cut that outlasts the battery. A wired network cable is steadier than Wi-Fi.
4. Install Docker: `sudo apt install docker.io docker-compose-v2`, then
   `sudo usermod -aG docker $USER` and sign out and back in.
5. Keep it up to date on its own: `sudo apt install unattended-upgrades`.

### 2. The app

```
git clone https://github.com/kenntankerus/CrapsTrainer.git
cd CrapsTrainer
mkdir -p data voice_packs          # made by you, so the app (user 1000) can write to them
cp .env.example .env
nano .env                          # fill it in as you go through the steps below
```

### 3. Move kbmoto.net's DNS to Cloudflare (free)

1. Make a Cloudflare account, choose **Add a domain**, enter `kbmoto.net`, pick the Free plan.
2. Cloudflare shows two nameservers. At the company you bought the domain from, replace the
   domain's nameservers with those two. It can take from minutes to a day to switch over.

### 4. The tunnel

1. In the Cloudflare dashboard: **Zero Trust → Networks → Tunnels → Create a tunnel**, type
   *Cloudflared*, name it `craps`.
2. It shows an install command with a long token in it. Copy just the token into `.env` as
   `CLOUDFLARE_TUNNEL_TOKEN=...` (the `tunnel` container in `docker-compose.yml` uses it; you don't
   need to install anything else).
3. Add a **public hostname**: subdomain `craps`, domain `kbmoto.net`, service type `HTTP`,
   URL `web:8000`.
4. In `.env`, set `CRAPS_BASE_URL=https://craps.kbmoto.net` and `CRAPS_TRUST_PROXY=1`.

### 5. Email for the sign-in links

Mail sent straight from a home connection lands in spam, so the links go through an email service.
[Resend](https://resend.com) has a free tier that covers a hobby site (Postmark, Amazon SES or
Brevo work the same way):

1. Make an account and add the domain `kbmoto.net`.
2. It lists a few DNS records (for SPF, DKIM and the like). Add each one under **DNS** in the Cloudflare
   dashboard, exactly as shown, and wait for Resend to mark the domain verified.
3. Make an API key. In `.env`, `CRAPS_SMTP_HOST=smtp.resend.com`, `CRAPS_SMTP_USER=resend`,
   `CRAPS_SMTP_PASSWORD=` the API key, and `CRAPS_MAIL_FROM=Craps Trainer <login@kbmoto.net>`.

### 6. The bot check (optional, recommended with open sign-up)

Cloudflare dashboard → **Turnstile → Add widget**, hostname `craps.kbmoto.net`. Put the site key
and secret key into `.env` (`CRAPS_TURNSTILE_SITE_KEY`, `CRAPS_TURNSTILE_SECRET`).

### 7. Start it

```
docker compose up -d --build
docker compose logs -f web          # Ctrl+C to stop watching
```

Open https://craps.kbmoto.net and sign in. Then bring in your desktop history: copy
`dice_throws.csv`, `dice_crops/` (and `craps_game_state.json` if you have it) into the
`CrapsTrainer` folder on the laptop and run

```
docker compose run --rm -v "$PWD:/import:ro" web python -m web.import_data you@example.com \
    --csv /import/dice_throws.csv --crops /import/dice_crops --game /import/craps_game_state.json
```

Docker restarts both containers after a reboot or a crash on its own.

### 8. Backups

Everything lives in `data/`: the database (`craps.db`), dice pictures and voice clips. Make a safe
copy of the database every night, and copy it somewhere that isn't the laptop. With `crontab -e`:

```
30 3 * * * cd ~/CrapsTrainer && docker compose exec -T web python -m web.backup >> data/backup.log 2>&1
```

That keeps the newest 14 copies in `data/backups/`. To get them off the laptop, install `rclone`, set
up any cloud storage with `rclone config` (Cloudflare R2, Backblaze B2, Google Drive...), and add
a second line, for example:

```
45 3 * * * rclone sync ~/CrapsTrainer/data remote:craps-backup --exclude "voice_cache/**"
```

To restore: stop the app, copy a backup over `data/craps.db`, start it again.

### Updating

```
cd ~/CrapsTrainer && git pull && docker compose up -d --build
```

### If it outgrows the laptop

Rent any small Linux server, install Docker, copy the `CrapsTrainer` folder (with `data/` and
`.env`) across, run `docker compose up -d`, and stop the laptop's copy. The tunnel token moves with
`.env`, so the address doesn't change.

## The camera

**On the page.** *Start camera* (in the settings bar) asks the browser for this device's camera, so it works
for anyone signed in, on any device, with nothing set up on the server. *Picture* shows it in a panel that
floats over the page (drag it by its title). For most accounts the picture never leaves the device and the
dice are tapped in; for the accounts in `CRAPS_CAMERA_USERS` the page reads them (next part).

- The first start asks to allow the camera. Browsers only offer a camera to an `https` page (or
  `localhost`, so the `127.0.0.1:8001` test copy works too). If it was refused, allow it again in the
  browser's site settings.
- It starts the camera facing away from the screen (a phone's back camera). Once allowed, a list of the
  device's cameras appears beside the button when there's more than one, and the choice is kept on that device.
- *Turn* turns the picture a quarter turn at a time, for a camera on its side like the Brio. Each camera
  keeps its own turn.
- "In use by another app" on the table's laptop can mean the server is holding the Brio: its own camera
  (below) keeps running after the page closes, so restart the app to free it.

**Reading the dice.** For the accounts in `CRAPS_CAMERA_USERS` (with open sign-up, nobody else should be
able to look through it), *Start camera* also starts the dice reader on the server: the page sends it the
picture, full size as JPEG, up to 10 times a second (the next once the last has arrived, so a slow connection
sends fewer). The server reads them with `dice_vision.py`, as it read its own webcam, and the status beside
the button says what it sees and how many pictures a second get through.

- **Each camera has its own settings**, kept for each account (`camera_profiles` in the database, the camera named
  as the browser names it). A camera with none saved reads with the defaults, `data/dice_settings.json`, tuned for
  the owner's Brio. *Settings* in the picture's panel shows them as JSON to change; saved, the reader uses them at
  once. They hold the reader's numbers (no `rotate`: the page turns its picture), `page_camera`, `white_balance`,
  `use_model` (false leaves the dice model out: it only knows the dice it learned from), and `felt_bgr` /
  `felt_level` for evening out the brightness. The server runs one reader at a time, which takes the settings of
  the camera it's reading.
- **Set up** (in the picture's panel) measures a camera and the table instead of asking for numbers
  (`web/camera_setup.py`), and saves its settings once two dice read right:
  1. *Turn* the picture so the back wall is at the top.
  2. *Empty table*: the page sets the camera's exposure where the browser can (a short exposure, about 1/30 s,
     with the gain searched until the felt is as bright as the reader was tuned on: `felt_level`, 88); focus finds
     the table and is held. Then the server finds the felt (its colour, judged tightly enough that light wood of
     the same hue isn't felt) and sets the search area to it plus a margin for dice at a wall. Things lying on the
     felt (big and solid, like a laptop) are left out, then anything the reader takes for a die on the empty
     table, each time moving in the edge that loses the least felt. A die-sized spot with felt all round it is
     taken for a die left on the table, and said. The dashed line's edges can be dragged.
  3. *Two dice*: their colour (hue_low to hue_high, wrapping round through red for red dice), their size
     (min_die_area, two_dice_area) and how bright the pips are against them (white_level), then both must read
     with those settings, and the empty table is checked again with them. `use_model` stays on only for dice the
     colour of the defaults' (the model knows the owner's green dice).
  4. *Save*. Cancel keeps what the camera had.
- **Evening out**: once a camera is set up (it has `felt_bgr`), the server scales each of its pictures' colours so
  the felt comes out as measured, at `felt_level`: for cameras the browser can't set (Firefox, Safari), whose
  own exposure and white balance wander. Measured about once a second and changed only when off by a tenth (each
  change looks like movement to the reader); a camera the page set is near enough and left alone. The status says
  when it's brightening or darkening. It can't bring back dice already burned white.
- The page sends the picture as it shows it, turned by *Turn*, and the server's `rotate` is left out. Turn
  the Brio's picture so the back wall is at the top and the shooter's end at the bottom: `roi` and `nadir`
  are measured in that picture.
- The page sets the camera's controls from the server's settings before sending: `white_balance`, and
  `page_camera` in `data/dice_settings.json`, in the browser's names (read afresh, no restart needed). Left
  automatic, the Brio brightens the picture for the dark felt until the dice burn out white. For the Brio in
  Chrome on Windows: `{"exposureMode": "manual", "exposureTime": 312.5, "exposureCompensation": 110,
  "focusMode": "manual", "focusDistance": 15}` gives the brightness the reader was tuned on, at 30 pictures a
  second. There Chrome's `exposureCompensation` is the camera's gain: give it every time, or manual exposure
  keeps whatever gain the camera was left with and the same exposure comes out anywhere from black to right.
  `exposureTime` 625 halves the frame rate (17 a second) and blurs rolling dice more. Chrome and Edge can set
  these on many webcams, Firefox can't; the camera's line in the picture panel says what was set. Stopping the
  camera puts its automatic modes back.
- The search area (`roi`) has to stop short of the laptop at the near end: its lit screen read as two dice,
  and changing all the time, it kept "throwing". Here it ends at 1530.
- The picture shows what the reader sees: a box round each die it found (green with the number it read,
  yellow "?" for one it can't read), and the search area (`roi`) as a dashed line with the rest shaded.
  *Mask* (in the picture's panel) shows the newest picture as the reader's mask instead: white wherever it
  could find a die, so anything white on an empty table (a lit screen, a patch of glare) is trouble.
- With no picture for 10 seconds (the page closed, or the device asleep) the reader stops; a page that's
  still open starts it again with its next picture. Only one page reads at a time: starting the camera on
  another device moves the reading there.

**The server's own webcam.** The reader can still watch a webcam plugged into the server, as it did before
the page had a camera, but the page no longer starts it or has its Tune window. The notes below are kept for
the settings, which the page's pictures use too.

**Try it without a webcam** (on your own computer, after `pip install -r requirements-web.txt`):

```
CRAPS_CAMERA=demo CRAPS_CAMERA_USERS=you@example.com python -m web
```

A pretend camera throws drawn dice every few seconds, read by the same `dice_vision.py` code.

**On the laptop**, add to `.env` and restart with `docker compose up -d`:

```
CRAPS_CAMERA=0
CRAPS_CAMERA_USERS=you@example.com
COMPOSE_FILE=docker-compose.yml:docker-compose.camera.yml
```

The last line hands the webcam to the container, where it is always `/dev/video0` (so `CRAPS_CAMERA=0`).
If the laptop's own camera is also there, the Brio may not be `/dev/video0` on the laptop: name it by its
fixed name in `/dev/v4l/by-id/` (`ls /dev/v4l/by-id/`; the picture is the one ending `video-index0`):

```
CRAPS_CAMERA_DEVICE=/dev/v4l/by-id/usb-046d_Logitech_BRIO_XXXXXXXX-video-index0
```

**Tuning.** The reading settings live in `data/dice_settings.json`. The Tune window on the page changes
most of them live (and Retrain builds the dice model there, as `python -m web.train` does). Two notes from
tuning the Brio on Linux:

- Exposure is in tenths of a millisecond there. The Windows -3 (1250) gave only 4 pictures a second;
  400 with gain 70 gives about 14 (the most this laptop decodes at 1920x1080) and still reads the dice.
- The search area, `roi` ([left, top, right, bottom] of the picture as read), keeps the reader on the felt:
  the wood around it was read as dice. Take in the walls, or a die against one gets cut off and misread.
- `white_balance` (kelvin) fixes the colours. Left automatic, they shift with what fills the picture: with
  the red felt filling it the dice came out yellow and weren't found. 4000 keeps the felt purple.
- `rotate` (0, 90, 180 or 270, clockwise) is for a camera turned on its side, to see more of a long table.
  It turns the picture back so the back wall is at the top and the shooter's left is the picture's left;
  `roi` is then measured in the turned picture. The page's pictures come turned by its *Turn* instead.
- `felt_hue` (OpenCV's 0-179; this red felt is 172) finds dice that glare has bleached to beige: a die-sized
  spot that isn't felt, with felt all round it. Without it only green counts, and a light over the table loses
  the dice under it. A die against a wall isn't ringed by felt, so that one still needs its green.
  Spots of sun through a window shade are brighter, paler, pinker felt and stay felt (`FELT_HUE_RANGE`,
  `FELT_SAT_LOW` in `dice_vision.py`).
- `hue_high` 92 rather than 100: these dice are about 60-77, and a bluish strip by the laptop at 100 was read as a die.
- `nadir` ([x, y], the spot straight under the camera in the picture as read) and `tilt` are for dice seen at a
  slant. Away from that spot a die shows the side facing the camera as well, and the side's pips were counted
  with the top's (a 4 with three pips of its side read as unclear). The reader takes the most pips, farthest
  from `nadir` first, that make a face. `tilt` (the side face's width per pixel from `nadir`) trims a die to its
  top face, for the two-dice test (a slanted die is long, like two dice touching) and when no pips make a face.
  Here [540, 1020] and 0.025, found by trying values on pictures the camera couldn't read.
- `roi`, `rotate`, `white_balance`, `felt_hue`, `nadir` and `tilt` aren't in the Tune window yet: edit the file
  and restart the app.

Then press **Start camera** on the page. Throws go to the player picked at the top, on the tab you're
on: the Table settles bets, the Tracker only logs. Switching player or tab moves the camera with you.
A wrong read is fixed the usual way: tap the right dice (left die first, as in the picture) and
*Fix last*. When the camera can't read a throw, tap it in and the pictures are kept with it, as in
the desktop tracker.

**Tuning on Linux.** The settings from `dice_settings.json` come along, but Linux drives webcams
differently, so check them the first time:

1. Press **Tune**. Choose **Mask**: each die should be one solid white square and the carpet black.
   Raise *Saturation low* or *Brightness low* if the carpet speckles; lower them if the dice break up.
2. **Exposure** is in tenths of a millisecond on Linux. Your Windows setting of -3 (1/8 s) comes across
   as 1250. Lower it for a faster, darker picture, and add *Gain* to brighten it back up.
3. *White (pips)* is how bright a spot has to be to count as a pip.
4. **Save** keeps the settings in `data/dice_settings.json`. *Cancel* puts back what was there.

**Retrain the dice reader** (in the Tune window, or `docker compose exec web python -m web.train`)
learns from every camera throw's saved pictures, including the 560 brought over from the desktop.

## The stickman's voice

For each line, the server uses the best voice it can, in this order, and keeps what it makes:

1. **Your recorded packs**: `voice_packs/<voice>/` from `make_voice_pack.py`. Copy the folder over from
   Windows; `docker-compose.yml` already hands it to the server.
2. **ElevenLabs**, in the voices you designed there. In `.env`:
   ```
   CRAPS_ELEVENLABS_API_KEY=your key
   CRAPS_ELEVENLABS_VOICES=Precious (Gollum-ish)=VOICE_ID; Shean (Connery-ish)=VOICE_ID
   ```
   Use the names exactly as the Stickman list shows them; each voice's ID is on its page under *Voices*
   in ElevenLabs. The fixed lines are about 4,000 characters per voice, made once.
3. **Kokoro**, a free voice that runs on the server. Fetch its model once (about 340 MB):
   ```
   docker compose exec web python -m web.get_voices
   docker compose exec web python -m web.get_voices --prepare     # optional: record every line now (slow; overnight is fine)
   ```
4. **Microsoft's online voices** (edge-tts), as the desktop apps use them.
5. Failing all of those, the browser reads the line with its own voice.

The *Sound* window says which one the chosen voice is using.

**Personal calls** are made the first time they come up and kept, so "Pay the field, five dollars" is
only made once. With a free voice (Kokoro or Microsoft's) everyone gets them. In a voice that comes from
ElevenLabs they cost a little each, so only the accounts in `CRAPS_PREMIUM_USERS` (your camera users,
unless you set it) get them, and `CRAPS_ELEVENLABS_DAILY_CHARS` (20,000 by default) caps what goes to
ElevenLabs in a day. Anyone can turn them off in the *Sound* window.

**Room and crowd sounds** are made in the browser until you record real ones with ElevenLabs' sound
effects, once:

```
docker compose exec web python -m web.make_sounds
```

## Settings

All in `.env`; `.env.example` lists them. The ones not covered above: `CRAPS_CAMERA` and `CRAPS_CAMERA_USERS` (see The camera),
`CRAPS_FEEDBACK_EMAIL` (where the menu's feature requests and bug reports are emailed, with the sender as
Reply-To; the first camera user if empty. They're kept in the database's `feedback` table too), `CRAPS_DATA_DIR` (where the data
lives), `CRAPS_TTS=0` (skip edge-tts and let the browser speak), `CRAPS_VOICE_PACKS` and
`CRAPS_SITE_NAME`.

## How it fits together

| File | What it does |
|---|---|
| `web/app.py` | the routes: pages, sign-in, account, players, the table, the tracker, voice clips |
| `web/auth.py` | sign-in links, sessions, rate limits, the bot check, guests taking their history |
| `web/tables.py` | the craps table on the server: what `craps_game.py` does with clicks, done per request |
| `web/stats.py` | the tracker's numbers, as `dice_tracker_v2.py` shows them |
| `web/voice.py` | stickman clips: recorded packs, then ElevenLabs, Kokoro or edge-tts, made once and kept |
| `web/calls.py` | personal calls (payouts, names) and how the crowd reacts |
| `web/get_voices.py`, `web/make_sounds.py` | fetching Kokoro's model and recording lines; recording room and crowd sounds |
| `web/camera_profiles.py` | the dice reader's settings for each account's cameras |
| `web/camera_setup.py` | Set up: measuring the felt, the search area and the dice from the page's pictures |
| `web/camera.py` | the dice reader: runs `dice_vision.py` on the page's pictures (or the server's webcam), rolls each throw for whoever started it, tuning |
| `web/camera_demo.py` | the pretend camera, for trying it out and for the tests |
| `web/events.py` | live updates to open pages (server-sent events) |
| `web/train.py` | retraining the dice reader from the saved pictures |
| `web/db.py` | the SQLite database |
| `web/static/` | the page: `app.js`, the layout drawing in `layout.js`, sounds in `sound.js` |
| `web/import_data.py`, `web/backup.py` | bringing in the desktop data, and safe copies of the database |
