"""Board push scheduler — background thread that auto-pushes boards on schedule.

Runs as a daemon thread, waking every 60s to check if any board is due.
Each board has its own interval + optional "smart" mode (e.g. stock only
during A-share trading hours).

Thread-safety: a Lock guards the last_pushed timestamps so manual pushes
(from HTTP) and scheduled pushes don't race.
"""

import logging
import threading
import time
from datetime import datetime

from board import config_store

logger = logging.getLogger(__name__)

# A-share trading hours (used by smart mode)
# Morning 9:30-11:30, Afternoon 13:00-15:00, Mon-Fri
def _is_trading_hours(now=None):
    now = now or datetime.now()
    if now.weekday() >= 5:  # Sat=5, Sun=6
        return False
    h, m = now.hour, now.minute
    mins = h * 60 + m
    morning = 9 * 60 + 30 <= mins <= 11 * 60 + 30
    afternoon = 13 * 60 <= mins <= 15 * 60
    return morning or afternoon


class BoardScheduler:
    """Background scheduler that auto-pushes boards to the device."""

    def __init__(self):
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last_pushed = {}      # board_id -> epoch timestamp
        self._render_fn = None      # injected: render_board function
        self._switch_fn = None      # injected: device_switch_page (optional)

    def configure(self, render_fn, switch_fn=None):
        """Inject the render function (avoids circular import with app.py)."""
        self._render_fn = render_fn
        self._switch_fn = switch_fn

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="board-scheduler")
        self._thread.start()
        logger.info("Board scheduler started")

    def stop(self):
        self._stop.set()

    def mark_pushed(self, board_id):
        """Called after any push (manual or scheduled) to update timestamp."""
        with self._lock:
            self._last_pushed[board_id] = time.time()

    def _run(self):
        # Wait a bit on startup for the app to be ready
        time.sleep(10)
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                logger.exception("scheduler tick failed: %s", e)
            # Check every 60s
            self._stop.wait(60)

    def _tick(self):
        """Check due boards; push at most ONE per tick.

        The device has a single Screenshot slot, so pushing several due
        boards back-to-back would just have them overwrite each other.
        Picking the least-recently-pushed due board turns the schedule
        into an orderly rotation instead of a stampede. It also fixes the
        startup thundering herd (_last_pushed starts empty).
        """
        schedules = config_store.load()
        now = time.time()
        now_dt = datetime.now()

        due = []
        for board_id, cfg in schedules.items():
            if not cfg.get("enabled"):
                continue
            if cfg.get("smart") and not _is_trading_hours(now_dt):
                continue
            interval = int(cfg.get("interval_min", 60)) * 60
            elapsed = now - self._last_pushed.get(board_id, 0)
            if elapsed >= interval:
                due.append((elapsed, board_id))

        if not due:
            return

        # Most stale first — exactly one push per tick (60s spacing)
        due.sort(reverse=True)
        _, board_id = due[0]
        template = schedules[board_id].get("template", "")
        logger.info("scheduler: pushing %s/%s (%d due, picked most stale)",
                    board_id, template, len(due))
        self._push(board_id, template)

    def _push(self, board_id, template_id):
        if not self._render_fn:
            logger.warning("scheduler: no render_fn configured")
            return
        try:
            result = self._render_fn(board_id, template_id=template_id,
                                     push=True, auto_switch=True)
            # Mark on failure too: a device that's offline shouldn't wedge
            # the rotation onto one repeatedly-failing board. The next due
            # window will try again naturally.
            self.mark_pushed(board_id)
            if result.get("ok"):
                logger.info("scheduler: pushed %s OK", board_id)
            else:
                logger.warning("scheduler: push %s failed: %s (will retry "
                               "next window)", board_id, result.get("error"))
        except Exception as e:
            self.mark_pushed(board_id)
            logger.exception("scheduler: push %s exception: %s", board_id, e)


# Singleton
scheduler = BoardScheduler()
