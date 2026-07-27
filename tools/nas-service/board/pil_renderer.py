"""PIL-based board renderer — zero anti-aliasing for crisp e-ink text.

Replaces the Playwright/Chromium pipeline for text-heavy boards. Chromium's
font AA produces grey halos that dither into speckle on a 4-color e-ink panel.
PIL's 1-bit mode rasterizes glyphs with hard pixel edges (no grey), so the
ditherer receives pure BWRY input and the output stays clean.

Architecture:
  - Text is drawn on a separate "1" mode image (forced no-AA)
  - Color blocks (yellow fills, red borders) are drawn on an RGB image
  - The 1-bit text mask is composited as black onto the RGB layer
  - Result: pure BWRY pixels, zero grey halos

Layout uses absolute pixel coordinates (like firmware RawDraw), computed for
the 400x300 panel. Not as flexible as CSS, but the only way to get clean text.
"""

import logging
from PIL import Image, ImageDraw, ImageFont, ImageChops
from pathlib import Path

logger = logging.getLogger(__name__)

SCREEN_W = 400
SCREEN_H = 300

# Pure ink colors — must match epaper-dithering BWRY palette exactly.
# Off-colors dither into speckle.
C_BLACK = (0, 0, 0)
C_WHITE = (255, 255, 255)
C_RED = (255, 0, 0)
C_YELLOW = (255, 255, 0)


class PilBoardRenderer:
    """Renders boards with PIL (no Chromium, no AA)."""

    def __init__(self):
        self._fonts = {}
        self._font_path = _find_font()

    def _font(self, size):
        """Cached font loader."""
        if size not in self._fonts:
            try:
                self._fonts[size] = ImageFont.truetype(self._font_path, size)
            except Exception as e:
                logger.warning("font load failed: %s, using default", e)
                self._fonts[size] = ImageFont.load_default()
        return self._fonts[size]

    def render(self, draw_fn):
        """Run a draw function that paints onto a fresh canvas.

        draw_fn(draw_rgb, draw_text, fonts) - the board's layout code.
          draw_rgb: ImageDraw on the RGB color layer
          draw_text: ImageDraw on the 1-bit text layer (use fill=0 for black)
          fonts: callable(size) -> ImageFont
        Returns the composited RGB image.
        """
        rgb = Image.new("RGB", (SCREEN_W, SCREEN_H), C_WHITE)
        text = Image.new("1", (SCREEN_W, SCREEN_H), 1)  # 1=white(background)
        draw_rgb = ImageDraw.Draw(rgb)
        draw_text = ImageDraw.Draw(text)

        draw_fn(draw_rgb, draw_text, self._font)

        # Composite: where text layer is 0 (black text), paint black on RGB.
        # Convert 1-bit text to "L" mask first (255 where text=0, 0 where bg=1),
        # because ImageChops.invert doesn't work on "1" mode images.
        mask = text.convert("L").point(lambda v: 255 - v)
        black = Image.new("RGB", (SCREEN_W, SCREEN_H), C_BLACK)
        rgb.paste(black, mask=mask)
        return rgb


def _find_font():
    """Locate a CJK font on the system (Linux container or dev machine)."""
    candidates = [
        # Linux (NAS container) — Noto CJK installed via Dockerfile
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc",
        # Windows dev machine
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            logger.info("Using font: %s", p)
            return p
    logger.warning("No CJK font found, text will be boxes")
    return None


# Singleton
pil_renderer = PilBoardRenderer()
