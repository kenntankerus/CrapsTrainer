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
- Hosting plan: the old Windows laptop becomes an Ubuntu Server running Docker, at `craps.kbmoto.net` through a
  Cloudflare Tunnel. Already set up online: Cloudflare DNS for kbmoto.net, the tunnel (route `craps.kbmoto.net`
  -> `http://web:8000`), Turnstile, and Resend (domain verified). The laptop itself isn't set up yet.
- Never tried for real: building the Docker image, edge-tts, ElevenLabs and Kokoro (cloud sessions couldn't
  reach them), and the Brio webcam on Linux (needs tuning with the Tune window; Windows exposure -3 maps to 1250).

## Next up

1. **Dice sets** (waiting on the owner's exact holds):
   - The Hardway list (`HARDWAY_SETUPS` in `dice_stats.py`, shared with the desktop apps) wrongly offers all
     24 up/facing holds. A real hardway set has both dice held alike with 2, 3, 4 and 5 on the rolling axis
     (1 and 6 on the sides): only 2-3, 2-4, 3-2, 3-5, 4-2, 4-5, 5-3, 5-4 (face up - face toward the thrower).
   - Add a **Custom** set: up and facing for the left die and for the right die, logged like `Custom L6-2 R6-2`.
   - A Custom hold that is really a named set is logged under that name: match on what the set does (any
     hardway-set hold is Hardway; every rolling pair summing to 7 is All Sevens; left/right swapped is the same
     set). The owner will give the holds for 3-V, All Sevens, Parallel, Cross-Sixes and Axis.
   - The 107 throws logged as "Hardway 6-2" were both dice sixes up, twos facing: relabel them
     `Custom L6-2 R6-2` (or the named set it turns out to be), in `dice_throws.csv` and the import.
2. **Voices:** the owner will send which ElevenLabs voice ID goes with which character, for
   `CRAPS_ELEVENLABS_VOICES` in `.env`.
3. **When the laptop is set up:** follow `web/README.md` (Ubuntu, Docker, `.env`, `docker compose up -d --build`,
   import the desktop data, tune the camera, `python -m web.get_voices`, `python -m web.make_sounds`).
