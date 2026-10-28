# Craps Trainer on the web

The craps table and the throw tracker as a website: sign in with an emailed link, play from a
phone or a laptop, and keep every throw on your own server. It uses the same rules
(`craps_engine.py`), stats (`dice_stats.py`) and stickman (`stickman.py`) as the desktop apps,
which keep working as before.

What's in this first version:

* **Accounts with emailed sign-in links** (no passwords). The first link makes the account.
* **Guests**: add players to your table who play on your screen with their own bankroll and stats.
  *Send history* emails a guest a link that moves their throws into an account of their own.
* **The table**: every bet from the desktop game. Click or tap to bet, right-click or
  press-and-hold to take down, drag (mouse) or *Move it…* (touch) to move. Undo and Fix last work
  like Ctrl+Z and F2. Rolls settle on the server, so a phone and a laptop show the same table.
* **The tracker**: log throws without bets, and compare dice sets and positions against fair dice,
  with the Small / Tall / All runs and the totals chart.
* **The stickman**: the same lines and voices. Lines are made with edge-tts on the server (or played
  from `voice_packs/`), and the browser's own voice reads them if the server can't.
* Download all your throws as `dice_throws.csv`, or delete your account, from the menu.

The camera isn't in the web version yet. That's the next stage.

## Try it on your own computer

```
pip install -r requirements-web.txt
python -m web
```

Open http://localhost:8000. With no email settings the sign-in link is printed in the terminal
instead of sent: copy it into the browser.

To bring in what the desktop apps logged (run from the repo folder, with the server stopped or running):

```
python -m web.import_data kenntankerus@pm.me
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
docker compose run --rm -v "$PWD:/import:ro" web python -m web.import_data kenntankerus@pm.me \
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

## Settings

All in `.env`; `.env.example` lists them. The ones not covered above: `CRAPS_DATA_DIR` (where the data
lives), `CRAPS_TTS=0` (skip edge-tts and let the browser speak), `CRAPS_VOICE_PACKS` and
`CRAPS_SITE_NAME`.

## How it fits together

| File | What it does |
|---|---|
| `web/app.py` | the routes: pages, sign-in, account, players, the table, the tracker, voice clips |
| `web/auth.py` | sign-in links, sessions, rate limits, the bot check, guests taking their history |
| `web/tables.py` | the craps table on the server: what `craps_game.py` does with clicks, done per request |
| `web/stats.py` | the tracker's numbers, as `dice_tracker_v2.py` shows them |
| `web/voice.py` | stickman clips: recorded packs, else made with edge-tts and kept |
| `web/db.py` | the SQLite database |
| `web/static/` | the page: `app.js`, the layout drawing in `layout.js`, sounds in `sound.js` |
| `web/import_data.py`, `web/backup.py` | bringing in the desktop data, and safe copies of the database |
