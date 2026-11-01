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
- Never tried for real yet: the Brio webcam on Linux (camera lines in `.env` still off; needs tuning with the
  Tune window; Windows exposure -3 maps to 1250), edge-tts, ElevenLabs and Kokoro.

## Next up

1. **Dice sets: the named holds** (waiting on the owner's holds for 3-V, Parallel Sixes, Cross-Sixes and Axis).
   - Every throw logs its hold, the face toward the shooter included: `Straight Sixes 6-5` (both dice alike),
     `All Sevens L6-2 R1-5`, `Hardway 3-2`, `Custom L5-1 R5-1`. The web app's hold picker lists the holds that are
     the chosen set (`SET_HOLDS` in `dice_stats.py`); Custom picks each die. The owner asked for this knowing it
     may get busy in the tracker, and may simplify it later.
   - `set_name` logs a Custom hold that is a set under that set's name. Hardway is both dice alike with 2, 3, 4, 5
     rolling (8 holds); All Sevens is every rolling pair adding up to 7; a set in `NAMED_HOLDS` is matched on
     the same top pair, then the same pairs either way the dice turn, left and right swapped the same (`same_set`).
   - **Straight Sixes** is 6-2 or 6-5 on both dice. `L5-1 R5-1` rolls the same pairs with fives on top; the owner
     wants it kept Custom. "Parallel" is renamed **Parallel Sixes**.
   - Done in the server's database (backup from before: `data/craps-before-relabel-20261101.db`) and
     `dice_throws.csv`: old "Hardway 6-2" / "6-5" are `Straight Sixes 6-2` / `6-5`, "Hardway 5-1" is
     `Custom L5-1 R5-1`, "Parallel" is Parallel Sixes. Older throws of the other sets have no hold recorded.
   - Left: add the owner's holds to `NAMED_HOLDS`, then relabel any Custom throws that turn out to be named.
   - The desktop apps get the new set names and the 8 Hardway holds, but no hold picker for the other sets.
2. **Voices:** the owner will send which ElevenLabs voice ID goes with which character, for
   `CRAPS_ELEVENLABS_VOICES` in `.env`.
3. **Finish the server** (`web/README.md`): turn on the camera lines in `.env` and tune it, fetch the free voice
   (`docker compose exec web python -m web.get_voices`, then `--prepare`), record room and crowd sounds
   (`python -m web.make_sounds`, needs the ElevenLabs key), and add the backup crontab lines.
4. **The server is the table's screen** (it's the touch-screen laptop at the table): `ubuntu-desktop-minimal`,
   automatic login, and `~/.config/autostart/craps-kiosk.desktop` runs `~/.local/bin/craps-kiosk` (screen blanking,
   locking and sleep off, then Firefox on https://craps.kbmoto.net). **Kiosk mode is off for now**: Firefox opens as a
   normal window, with Terminal pinned to the dock, so the browser and consoles stay in reach while things get
   ironed out. When it's done, put `--kiosk` back on the `firefox` line in that script.
