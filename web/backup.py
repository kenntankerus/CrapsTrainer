"""
A safe copy of the database while the server runs:   python -m web.backup [folder]

Writes craps-<date>.db into the folder (data/backups by default) and keeps the newest 14.
Copy that folder somewhere off the machine (see web/README.md), or a dead disk takes every account with it.
"""

import os
import sqlite3
import sys
from datetime import datetime

from . import config

KEEP = 14


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    folder = os.path.abspath(argv[0] if argv else os.path.join(config.DATA_DIR, "backups"))
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, f"craps-{datetime.now():%Y%m%d-%H%M%S}.db")
    source = sqlite3.connect(config.DB_PATH)
    copy = sqlite3.connect(target)
    with copy:
        source.backup(copy)
    copy.close()
    source.close()
    old = sorted(f for f in os.listdir(folder) if f.startswith("craps-") and f.endswith(".db"))[:-KEEP]
    for name in old:
        os.remove(os.path.join(folder, name))
    print(f"Backed up to {target}" + (f", removed {len(old)} older copies" if old else ""))


if __name__ == "__main__":
    main()
