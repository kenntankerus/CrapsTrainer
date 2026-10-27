"""
make_voice_pack.py
------------------
Records a character's stickman lines with an ElevenLabs voice. The tracker and the
craps game play these recordings instead of the free Microsoft voice whenever they exist.

Setup:  1. In ElevenLabs, design a voice (Voices > Create a voice > Voice Design), save it
           and copy its voice ID.
        2. Put your API key in the environment (then open a new terminal):
               setx ELEVENLABS_API_KEY "your key"
Run:    python make_voice_pack.py "Precious (Gollum-ish)" VOICE_ID --try    (3 lines, played to you)
        python make_voice_pack.py "Precious (Gollum-ish)" VOICE_ID          (every line not recorded yet)

Clips go to voice_packs/<character>/. Running it again only records lines that are new or
have changed, so reruns are cheap. --redo records everything again (say, with a new voice).
Lines can carry stage directions like [whispers] or [gulps]: the newer ElevenLabs models act
them out, and they're left out for older models and for the free voice.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

import dice_tracker_v2 as tracker

API = "https://api.elevenlabs.io/v1"
MODELS = ("eleven_v4", "eleven_v3", "eleven_multilingual_v2")    # first one the account offers is used
DIRECTED = ("eleven_v4", "eleven_v3")                              # models that act out [stage directions]
MOOD_DIRECTIONS = {"hype": "[excited]", "down": "[sighs]"}         # for lines without a direction of their own
STABILITY = 0.5            # v3/v4 take 0.0 (most expressive), 0.5 or 1.0 (steadiest)
TRY_LINES = ("co_point_6", "out", "made_hard_8")


def request(path, key, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method="POST" if body is not None else "GET",
                                 headers={"xi-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        sys.exit(f"ElevenLabs said no ({error.code}): {detail}")
    except urllib.error.URLError as error:
        sys.exit(f"Couldn't reach ElevenLabs: {error.reason}")


def pick_model(key):
    offered = {m["model_id"] for m in json.loads(request("/models", key)) if m.get("can_do_text_to_speech")}
    for model in MODELS:
        if model in offered:
            return model
    sys.exit(f"None of {', '.join(MODELS)} is available on this account.")


def performance(text, mood, model):
    """The text as sent: stage directions kept (plus one for the mood) on models that act them out."""
    if model not in DIRECTED:
        return tracker.strip_tags(text)
    if text.startswith("[") or mood not in MOOD_DIRECTIONS:
        return text
    return f"{MOOD_DIRECTIONS[mood]} {text}"


def main():
    parser = argparse.ArgumentParser(description="Record a character's lines with an ElevenLabs voice.")
    parser.add_argument("character", choices=list(tracker.VOICES), help="the name as it shows in the voice list")
    parser.add_argument("voice_id", help="the ElevenLabs voice ID")
    parser.add_argument("--try", dest="trial", action="store_true", help="record and play just 3 lines")
    parser.add_argument("--redo", action="store_true", help="record every line again")
    parser.add_argument("--model", help=f"ElevenLabs model (default: the first available of {', '.join(MODELS)})")
    parser.add_argument("--yes", action="store_true", help="don't ask before spending credits")
    args = parser.parse_args()

    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        sys.exit("Set ELEVENLABS_API_KEY first:  setx ELEVENLABS_API_KEY \"your key\"  (then open a new terminal)")
    model = args.model or pick_model(key)

    folder = tracker.pack_folder(args.character)
    manifest_path = os.path.join(folder, "manifest.json")
    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        manifest = {}
    same_voice = manifest.get("voice_id") == args.voice_id and manifest.get("model") == model
    done = manifest.get("clips", {}) if same_voice and not args.redo else {}

    jobs = []
    for line_key, (variants, mood) in tracker.voice_lines(args.character).items():
        if args.trial and line_key not in TRY_LINES:
            continue
        for i, text in enumerate(variants):
            if args.trial and i:
                continue
            name, spoken = f"{line_key}_{i}", performance(text, mood, model)
            if args.trial or done.get(name) != spoken or not os.path.exists(os.path.join(folder, name + ".mp3")):
                jobs.append((name, spoken))
    if not jobs:
        print(f"All {len(done)} lines are already recorded in {folder}")
        return

    characters = sum(len(spoken) for _, spoken in jobs)
    print(f"{len(jobs)} lines to record with {model}, about {characters:,} characters of your ElevenLabs credit.")
    if not args.yes and input("Go ahead? [y/N] ").strip().lower() != "y":
        return

    os.makedirs(folder, exist_ok=True)
    manifest = {"voice_id": args.voice_id, "model": model, "clips": done}
    paths = []
    for n, (name, spoken) in enumerate(jobs, start=1):
        print(f"  {n:>3}/{len(jobs)}  {spoken}")
        audio = request(f"/text-to-speech/{args.voice_id}?output_format=mp3_44100_128", key,
                        {"text": spoken, "model_id": model, "voice_settings": {"stability": STABILITY}})
        path = os.path.join(folder, name + ".mp3")
        with open(path, "wb") as f:
            f.write(audio)
        paths.append(path)
        manifest["clips"][name] = spoken
        with open(manifest_path, "w") as f:           # after every line, so a stopped run keeps what it made
            json.dump(manifest, f, indent=2)
    print(f"Saved to {folder}")

    if args.trial:
        speaker = tracker.Speaker()
        speaker._play_all(paths, speaker.latest)
        print("Like it? Run again without --try to record the rest (these 3 are kept).")


if __name__ == "__main__":
    main()
