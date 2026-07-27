"""Playwright HTML → PIL Image screenshot engine.

Playwright's sync API is thread-affine: a browser instance MUST be created
and used from the same thread. Flask/gunicorn serve requests on worker
threads, so we funnel all Playwright work through a single dedicated
worker thread. Callers submit a job and block on its result.

Usage:
    from board.renderer import board_renderer
    img = board_renderer.render_html(html_string)  # -> PIL.Image (RGB, 400x300)
"""

import io
import logging
import queue
import threading

from PIL import Image

logger = logging.getLogger(__name__)

SCREEN_W = 400
SCREEN_H = 300


class BoardRenderer:
    """Runs all Playwright operations on one dedicated thread."""

    def __init__(self, width: int = SCREEN_W, height: int = SCREEN_H):
        self._width = width
        self._height = height
        self._playwright = None
        self._browser = None
        self._queue: "queue.Queue" = queue.Queue()
        self._worker = threading.Thread(target=self._run, daemon=True, name="board-playwright")
        self._worker.start()

    # ----- worker thread: owns the browser, executes one job at a time -----
    def _run(self):
        while True:
            job = self._queue.get()
            if job is None:
                self._teardown()
                return
            html, result_q = job
            try:
                img = self._render_impl(html)
                result_q.put(("ok", img))
            except Exception as e:
                logger.exception("board render failed")
                result_q.put(("err", e))

    def _ensure_browser(self):
        if self._browser is not None:
            try:
                # Liveness probe: try a no-op that touches the browser.
                self._browser.is_connected()
                return
            except Exception:
                logger.warning("Playwright browser died, relaunching")
                self._teardown()
        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            args=[
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--font-render-hinting=none",
            ]
        )
        logger.info("Playwright Chromium launched")

    def _render_impl(self, html: str) -> Image.Image:
        """Render at 4x then downscale + binarize for crisp e-ink edges."""
        self._ensure_browser()
        context = self._browser.new_context(
            viewport={"width": self._width, "height": self._height},
            device_scale_factor=4,
        )
        page = context.new_page()
        try:
            page.set_content(html, wait_until="networkidle")
            png_bytes = page.screenshot(
                full_page=False,
                clip={"x": 0, "y": 0, "width": self._width, "height": self._height},
                omit_background=False,
            )
        finally:
            context.close()
        hi = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        img = hi.resize((self._width, self._height), Image.LANCZOS)
        return img  # 不二值化,直接交给抖动器处理(测试对比)

    def _teardown(self):
        if self._browser:
            try: self._browser.close()
            except Exception: pass
            self._browser = None
        if self._playwright:
            try: self._playwright.stop()
            except Exception: pass
            self._playwright = None

    # ----- public API: submit job, block for result -----
    def render_html(self, html: str) -> Image.Image:
        result_q: "queue.Queue" = queue.Queue()
        self._queue.put((html, result_q))
        kind, payload = result_q.get()  # block until worker finishes
        if kind == "err":
            raise payload
        return payload

    def shutdown(self):
        self._queue.put(None)
        self._worker.join(timeout=5)


board_renderer = BoardRenderer()


def _binarize_for_ink(img: Image.Image) -> Image.Image:
    """Snap every pixel to the nearest of the 4 pure ink colors.

    Chromium's font anti-aliasing produces halo pixels around glyph edges
    (grey around black text, pink around red text, muddy-olive on yellow
    fills). On a 4-color e-ink panel these non-pure colors dither into
    speckle and tint edges with the wrong ink.

    Mapping each pixel to its nearest pure ink (#000/#fff/#f00/#ff0) by
    Euclidean RGB distance absorbs halos into their dominant ink:
      - black-text grey halo -> black or white (sharper edges, no speckle)
      - red-text pink halo   -> red or white (no red edge bleed)
      - yellow-fill AA       -> yellow or white (clean fill)
    Dithering then sees only pure BWRY input -> clean output.
    """
    px = img.load()
    w, h = img.size
    inks = [(0, 0, 0), (255, 255, 255), (255, 0, 0), (255, 255, 0)]
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            best = 0
            best_d = r * r + g * g + b * b
            for i, ink in enumerate(inks[1:], 1):
                dr, dg, db = r - ink[0], g - ink[1], b - ink[2]
                d = dr * dr + dg * dg + db * db
                if d < best_d:
                    best_d = d
                    best = i
            px[x, y] = inks[best]
    return img
