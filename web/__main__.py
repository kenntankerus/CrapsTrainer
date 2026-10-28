"""python -m web   starts the server (CRAPS_HOST and CRAPS_PORT choose where it listens)."""

import os

import uvicorn


def main():
    uvicorn.run("web.app:app", host=os.environ.get("CRAPS_HOST", "127.0.0.1"),
                port=int(os.environ.get("CRAPS_PORT", "8000")), proxy_headers=False, log_level="info")


if __name__ == "__main__":
    main()
