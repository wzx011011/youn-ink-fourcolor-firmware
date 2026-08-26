"""Schedule config persistence — JSON file in the data volume.

Stores per-board push schedules (enabled, template, interval, smart mode).
Survives container restarts (lives in /data which is a mounted volume).
"""

import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# Config file location: /data volume (persisted across container restarts)
DATA_DIR = Path(os.environ.get("HISTORY_DIR", "/data/uploads")).parent
CONFIG_PATH = DATA_DIR / "schedule_config.json"

_lock = threading.Lock()

# Default schedules applied on first run / missing keys
DEFAULT_SCHEDULES = {
    "stock": {
        "enabled": True, "template": "dashboard",
        "interval_min": 10, "smart": True,   # smart = only trading hours
    },
    "weather": {
        "enabled": True, "template": "card",
        "interval_min": 60, "smart": False,
    },
    "news": {
        "enabled": False, "template": "list",
        "interval_min": 360, "smart": False,
    },
    "almanac": {
        "enabled": True, "template": "classic",
        "interval_min": 720, "smart": False,
    },
}


def load():
    """Load schedules from disk, merging defaults for missing keys.

    Entries for boards not in DEFAULT_SCHEDULES (newly registered boards)
    are preserved — dropping them would silently lose their config on the
    next save(). Defaults only fill in gaps.
    """
    with _lock:
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    saved = json.load(f).get("schedules", {})
            except Exception as e:
                logger.warning("config load failed, using defaults: %s", e)
                saved = {}
        else:
            saved = {}

        fallback = {"enabled": False, "template": "",
                    "interval_min": 60, "smart": False}
        merged = {}
        # Keep every board already saved on disk (unknown ones included)
        for bid, cfg in saved.items():
            base = dict(DEFAULT_SCHEDULES.get(bid) or fallback)
            base.update(cfg)
            merged[bid] = base
        # Fill in defaults for built-in boards absent from disk
        for bid, default in DEFAULT_SCHEDULES.items():
            if bid not in merged:
                merged[bid] = dict(default)
        return merged


def save(schedules):
    """Persist schedules to disk."""
    with _lock:
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump({"schedules": schedules}, f, ensure_ascii=False, indent=2)
            logger.info("schedule config saved to %s", CONFIG_PATH)
        except Exception as e:
            logger.error("config save failed: %s", e)
            raise


def update_board(board_id, **kwargs):
    """Update one board's schedule fields (e.g. enabled=True)."""
    schedules = load()
    if board_id not in schedules:
        schedules[board_id] = dict(DEFAULT_SCHEDULES.get(board_id, {
            "enabled": False, "template": "", "interval_min": 60, "smart": False
        }))
    schedules[board_id].update(kwargs)
    save(schedules)
    return schedules[board_id]
