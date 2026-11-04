# Craps Trainer

Practice craps with your own dice: a craps table that settles real bets, a throw tracker that compares
dice sets and positions against fair dice, a webcam that reads the dice, and a stickman who calls the rolls.

## Layout

- **Desktop apps (Windows, Tkinter):** `craps_game.py` (the table), `dice_tracker_v2.py` (the tracker),
  `dice_camera_test.py` (camera tuning), `train_dice_model.py`, `make_voice_pack.py` (ElevenLabs voice packs).
  They keep working; the web app is the way forward.
- **Shared logic (no window code):** `craps_engine.py` (rules, payouts, `run_summary`), `dice_stats.py`
  (seven rates, Small/Tall/All runs, shooter status, dice set and position names), `stickman.py` (every line,
  the voices, `croupier_call`, `stickman_call`), `dice_vision.py` (reading dice; Windows DirectShow or Linux V4L2).
- **Web app:** `web/` (FastAPI + SQLite, `python -m web`). `web/README.md` is the full setup guide and the
  map of its files. Pages are plain JS modules in `web/static/` with no build step.
- **Tests:** `python -m pytest tests` (needs `requirements-web.txt` plus pytest and httpx). GitHub Actions runs
  them on pull requests and on pushes to `main`.

## Conventions

- Comments and docs explain the why, in plain words, matching the existing files. Money is in cents.
- Desktop and web share the logic modules: changing them must keep the Tkinter apps working (they import
  names from `dice_tracker_v2`, e.g. `tracker.VOICES`). Tkinter isn't installed in cloud sessions, so check
  the desktop files with a stub `tkinter` module and compare outputs before and after.
- The repo is public: keep email addresses, keys and tokens out of it. Secrets live only in `.env` on the server.

## Where things stand

- Merged: PR #1 (web app, accounts with emailed sign-in links, guests, table, tracker, camera on the server,
  live updates, Docker + Cloudflare Tunnel setup, CI) and PR #2 (voice engines: packs > ElevenLabs > Kokoro >
  edge-tts > browser; personal calls; the browser sound mix and crowd reactions).
- **Live at https://craps.kbmoto.net.** The server is the old Dell Inspiron 15-3567, now Ubuntu Server (booting
  in Legacy BIOS mode, Secure Boot off; Dell's Auto OS Recovery is turned off) running the app with
  `docker compose` from `~/src/CrapsTrainer`, behind a Cloudflare Tunnel (route `craps.kbmoto.net` -> `http://web:8000`).
  `.env` there holds the Resend SMTP key, the Turnstile keys and the tunnel token. Sign-in emails work.
- The Tkinter apps were confirmed to still run on Windows after the shared-logic refactor, before the wipe.
- The desktop history is imported into the owner's account: 424 throws, the craps game's saved table and 716 dice
  pictures. Tested on Android and on a PC.
- Sign-in links can be opened on any device: the page that asked shows a 4-digit code and waits, and the link's page
  offers to sign that device in (or this one instead). Tested working: laptop asks, phone approves.
- Scripts and styles are sent `no-cache`, and Cloudflare's Browser Cache TTL is set to **Respect Existing Headers**
  (its 4-hour default kept old copies of `app.js` in browsers, which broke the new sign-in). Leave it that way.
- The table has a **Virtual roll** button: the server throws random dice that settle bets but are stored with
  source `virtual`, which the tracker, the stats, the throw counts and the export leave out.
- When the camera starts, dice already on the table are taken as the starting picture, not a throw.
- **Dice sets.** A hold is "face up - face toward you" for each die, and every throw logs its hold:
  `Straight Sixes 6-5` (both dice alike), `3-V L3-5 R3-6`, `Hardway 3-2`, `Custom L5-1 R5-1`. The web app's hold
  picker lists the holds that are the chosen set (`SET_HOLDS` in `dice_stats.py`); Custom picks each die. The owner
  asked for this knowing it may get busy in the tracker, and may simplify it later.
  - The owner's holds (`NAMED_HOLDS`): 3-V L3-5 R3-6, 3-A L3-1 R3-5, 2-V L2-6 R2-3, 2-A L2-4 R2-1, Straight Sixes
    6-2 on both, Parallel Sixes 6-3 on both, Cross-Sixes L6-2 R6-3 and L6-2 R6-4 and both swapped. The same hold
    with the dice turning the other way is the same set (`same_set`: same top pair, the same pairs in either order),
    so each set has two holds and Cross-Sixes eight. Swapping the dice is a different hold, and for V and A sets a
    different set (2-V swapped is 2-A), so it is never matched. Hardway is both dice alike with 2, 3, 4, 5 rolling
    (8 holds); All Sevens is every rolling pair adding up to 7 (24). No hold is in two sets.
  - Axis was dropped: it's All Sevens. `L5-1 R5-1` rolls Straight Sixes' pairs with fives on top; the owner
    wants it kept Custom.
  - `set_name` logs a Custom hold that is a set under that set's name, on the server, for every way a throw comes in.
  - Relabelled in the server's database (backup from before: `data/craps-before-relabel-20261101.db`) and
    `dice_throws.csv`: old "Hardway 6-2" / "6-5" are `Straight Sixes 6-2` / `6-5`, "Hardway 5-1" is
    `Custom L5-1 R5-1`, "Parallel" is Parallel Sixes. Older 3-V, All Sevens, Cross-Sixes and Parallel Sixes throws
    have no hold recorded.
  - The desktop apps get the set names and the 8 Hardway holds, but no hold picker for the other sets.
- **The camera is the device's** (PR #10, live; confirmed opening the table laptop's webcam): *Start camera* opens the camera of whatever the page is
  on with the browser's `getUserMedia` (needs https or localhost) and draws it, turned by *Turn* (kept per camera on
  that device), in the floating Camera panel. It doesn't read the dice yet: the owner wants camera access first and
  reading later. The page no longer starts the server's webcam or has its Tune window; the server's camera code,
  API and tests are untouched, for when reading comes back.
- **The server's camera reader**, as tuned, for when reading comes back: the Brio (by its `/dev/v4l/by-id` name in
  `CRAPS_CAMERA_DEVICE`), turned a quarter turn on its
  mount so the picture's long side runs along the table. Tuned in the server's `data/dice_settings.json`: `rotate` 270
  (turns the picture back: back wall at the top, the shooter's end at the bottom), `white_balance` 4000 (automatic
  white balance turned the dice yellow once the red felt filled the picture), exposure 400 + gain 70 (~14 pictures a
  second), `roi` [0, 80, 1080, 1700] in the turned picture (both side walls and the back wall in, down to the laptop at
  the near end; the camera has to be that high for the table's width to fit). Glare: a light above (it can't be
  moved) bleaches dice in the middle of the table to beige; `felt_hue` 172 finds them as die-sized spots ringed by
  felt, and their pips still read. Sun through the window shade lands on the felt in die-sized spots; they count as felt
  (pinker and paler, where the bleached die goes orange). `hue_high` is 92: a bluish strip by the laptop at 100 read as a die. Dice at the far end are seen at a
  slant and show a side face too: the reader takes the most pips farthest from `nadir` [540, 1020] that make a
  face, and `tilt` 0.025 trims a die to its top for the two-dice test. On the 24 unread pictures of two runs:
  9 read right without this, 15 with. At night (a lamp) the dice look nearly black and are found as not-felt
  spots, not by green. Still missed: dice pressed against a side wall (at night neither green nor ringed by felt). The dice model is trained on the
  server (780 pictures, 99% right when sure). Movement is checked against the picture 0.25 s earlier
  (`MOTION_SPAN`): at 14 a second a slow slide looked still and a moving die got read. Runs before turning it:
  10 of 15 read right, then 29 of 35 (the misses were dice out of the picture, or cut off at a wall).
- The camera keeps running on the server after the page is closed: stop it first, or restart the app.
- **Table tab layout** (on a computer screen it fits the window, no scrolling): settings bar with the camera buttons;
  a row of dice card (46px dice), Last roll (Fix last / Undo / Virtual roll and the message line on its heading's
  line, what got paid under them) and the bankroll tile; then the layout, which stretches to fill its tile (boxes
  grow, writing keeps its size; chips are placed by each box's own shape), with Rolls (270px) beside it; then a
  one-row chip tray across the full width. The camera picture floats in a panel dragged by its title.
- **Room noise is off for now** (`ROOM_NOISE = false` in `app.js`, which also hides its switch and slider). The
  owner asked for it; crowd reactions still play.
- Testing changes: `python -m web` from the server's checkout listens on `127.0.0.1:8001` and serves `web/static`
  straight from the files; reach it from the dev laptop with `ssh -N -L 8001:127.0.0.1:8001 kenn@192.168.1.185`.
- Never tried for real yet: edge-tts, ElevenLabs and Kokoro.

## Next up

1. **Voices:** the owner will send which ElevenLabs voice ID goes with which character, for
   `CRAPS_ELEVENLABS_VOICES` in `.env`.
2. **Finish the server** (`web/README.md`): fetch the free voice
   (`docker compose exec web python -m web.get_voices`, then `--prepare`), record room and crowd sounds
   (`python -m web.make_sounds`, needs the ElevenLabs key), and add the backup crontab lines.
3. **The server is the table's screen** (it's the touch-screen laptop at the table): `ubuntu-desktop-minimal`,
   automatic login, and `~/.config/autostart/craps-kiosk.desktop` runs `~/.local/bin/craps-kiosk` (screen blanking,
   locking and sleep off, then Firefox on https://craps.kbmoto.net). **Kiosk mode is off for now**: Firefox opens as a
   normal window, with Terminal pinned to the dock, so the browser and consoles stay in reach while things get
   ironed out. When it's done, put `--kiosk` back on the `firefox` line in that script.
