# The Craps Trainer web server. See web/README.md.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    CRAPS_DATA_DIR=/app/data CRAPS_VOICE_PACKS=/app/voice_packs CRAPS_HOST=0.0.0.0 CRAPS_PORT=8000

WORKDIR /app
COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt

# the game rules, stats, stickman lines and dice reader are shared with the desktop apps
COPY craps_engine.py dice_stats.py stickman.py dice_vision.py train_dice_model.py dice_settings.json ./
COPY web ./web

# the camera is /dev/video0, which belongs to the video group
RUN useradd --create-home --uid 1000 --groups video craps && mkdir -p /app/data && chown craps /app/data
USER craps
VOLUME /app/data
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/config', timeout=4)"
CMD ["python", "-m", "web"]
