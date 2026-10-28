"""Shared by the tests: a throwaway data folder, and sign-in emails caught instead of sent."""

import os
import sys
import tempfile

os.environ["CRAPS_DATA_DIR"] = tempfile.mkdtemp(prefix="craps-test-")
os.environ["CRAPS_TTS"] = "0"
os.environ["CRAPS_SMTP_HOST"] = ""
os.environ["CRAPS_TURNSTILE_SECRET"] = ""
os.environ["CRAPS_CAMERA"] = ""
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from web import config, mail  # noqa: E402

SENT = []


@pytest.fixture(autouse=True)
def capture_mail(monkeypatch):
    SENT.clear()
    monkeypatch.setattr(config, "LINKS_PER_IP", 1000)       # every test signs in from the same address
    monkeypatch.setattr(config, "LINKS_PER_EMAIL", 1000)    # and some sign the same account in more than once
    monkeypatch.setattr(mail, "send", lambda to, subject, text, html=None: SENT.append((to, subject, text)))
