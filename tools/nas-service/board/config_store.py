"""Schedule config persistence — JSON file in the data volume.

Stores per-board push schedules (enabled, template, interval, smart mode)
plus top-level extras (AI provider credentials). Survives container
restarts (lives in /data which is a mounted volume).

Concurrency: one module lock guards load/save/update_board/update_extras
so read-modify-write cycles are atomic. Writes go to a tmp file + os.replace
so readers never see a torn file; the file is chmod 0600 (it may hold AI keys).
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


def _load_doc_locked() -> dict:
    """Read the whole config doc (caller must hold _lock)."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                doc = json.load(f)
            if isinstance(doc, dict):
                return doc
        except Exception as e:
            logger.warning("config load failed, using defaults: %s", e)
    return {}


def _save_doc_locked(doc: dict):
    """Atomic write (caller must hold _lock): tmp file + os.replace."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(tmp, 0o600)  # 文件可能含 AI Key,仅属主可读写
    except OSError:
        pass  # Windows 开发机上 chmod 语义不完整,忽略
    os.replace(tmp, CONFIG_PATH)
    logger.info("schedule config saved to %s", CONFIG_PATH)


def load():
    """Load schedules from disk, merging defaults for missing keys.

    Entries for boards not in DEFAULT_SCHEDULES (newly registered boards)
    are preserved — dropping them would silently lose their config on the
    next save(). Defaults only fill in gaps.
    """
    with _lock:
        saved = _load_doc_locked().get("schedules", {})

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
    """Persist schedules to disk (preserving top-level extras like AI keys)."""
    with _lock:
        doc = _load_doc_locked()
        doc["schedules"] = schedules
        _save_doc_locked(doc)


def update_board(board_id, **kwargs):
    """Update one board's schedule fields (e.g. enabled=True).

    Lock held across load-modify-save so concurrent updates can't clobber
    each other.
    """
    with _lock:
        doc = _load_doc_locked()
        schedules = doc.get("schedules", {})
        if board_id not in schedules:
            schedules[board_id] = dict(DEFAULT_SCHEDULES.get(board_id, {
                "enabled": False, "template": "", "interval_min": 60, "smart": False
            }))
        schedules[board_id].update(kwargs)
        doc["schedules"] = schedules
        _save_doc_locked(doc)
        return schedules[board_id]


def update_extras(update: dict):
    """Merge top-level keys (e.g. AI provider credentials) into the config.

    Same lock + atomic write as the schedule API, and unlike schedule
    writes it keeps unrelated top-level keys intact.
    """
    with _lock:
        doc = _load_doc_locked()
        doc.update(update)
        _save_doc_locked(doc)
