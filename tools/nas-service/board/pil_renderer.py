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
from PIL import Image, ImageDraw, ImageFont
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

        draw_fn(draw_rgb, draw_text, draw_red, fonts) - the board's layout code.
          draw_rgb: ImageDraw on the RGB color layer (blocks, lines, icons)
          draw_text: ImageDraw on the 1-bit black text layer (fill=0)
          draw_red: ImageDraw on the 1-bit red text layer (fill=0) — same
                    hard-edge glyphs, composited as pure red. Use for
                    semantic accents (涨、忌、警告), not decoration.
          fonts: callable(size) -> ImageFont
        Returns the composited RGB image.
        """
        rgb = Image.new("RGB", (SCREEN_W, SCREEN_H), C_WHITE)
        text = Image.new("1", (SCREEN_W, SCREEN_H), 1)  # 1=white(background)
        red = Image.new("1", (SCREEN_W, SCREEN_H), 1)
        draw_rgb = ImageDraw.Draw(rgb)
        draw_text = ImageDraw.Draw(text)
        draw_red = ImageDraw.Draw(red)

        draw_fn(draw_rgb, draw_text, draw_red, self._font)

        # Composite each 1-bit text layer through an "L" mask (255 where the
        # glyph is, 0 elsewhere) so glyph edges stay hard — no AA grey that
        # would dither into speckle on the panel.
        for layer, color in ((text, C_BLACK), (red, C_RED)):
            mask = layer.convert("L").point(lambda v: 255 - v)
            rgb.paste(Image.new("RGB", (SCREEN_W, SCREEN_H), color), mask=mask)
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
    # fail-fast:静默返回 None 会退到像素默认字体,中文全是豆腐块还被
    # 正常推上屏。宁可启动失败,也要让部署者立刻看到缺字体。
    raise RuntimeError(
        "未找到 CJK 字体,中文将渲染为方块。请安装 fonts-noto-cjk"
        "(Docker 镜像已自带),或把 NotoSansCJK-Regular.ttc 放到以上候选路径之一"
    )


# Singleton
pil_renderer = PilBoardRenderer()


# ============================================================
# Shared text helpers — measure by PIXELS, never by char count.
# CJK glyphs are ~1em wide but latin/digits/punctuation are not,
# so `text[:N]` overflows. These helpers are the fix.
# ============================================================

def text_w(draw, text, font):
    """Pixel width of text in the given font."""
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def draw_centered(draw, text, font, cx, y, fill=0):
    """Draw text horizontally centered at cx (y = top)."""
    w = text_w(draw, text, font)
    draw.text((cx - w // 2, y), text, font=font, fill=fill)


def fit_text(draw, text, font, max_w):
    """Truncate text (with … ) so it fits max_w pixels. Pixel-accurate."""
    if text_w(draw, text, font) <= max_w:
        return text
    ell = "…"
    ell_w = text_w(draw, ell, font)
    out = []
    w = 0
    for ch in text:
        cw = text_w(draw, ch, font)
        if w + cw + ell_w > max_w:
            break
        out.append(ch)
        w += cw
    return "".join(out) + ell if out else ell


def wrap_text(draw, text, font, max_w, max_lines=2):
    """Greedy pixel-based wrap into at most max_lines lines.

    If text doesn't fit, the last line is truncated with … .
    Returns a list of line strings.
    """
    lines = []
    cur = []
    cur_w = 0
    i = 0
    while i < len(text):
        ch = text[i]
        cw = text_w(draw, ch, font)
        if cur and cur_w + cw > max_w:
            lines.append("".join(cur))
            if len(lines) == max_lines - 1:
                # Last allowed line: fit everything that's left, truncated.
                lines.append(fit_text(draw, text[i:], font, max_w))
                return lines
            cur, cur_w = [], 0
            continue
        cur.append(ch)
        cur_w += cw
        i += 1
    if cur:
        lines.append("".join(cur))
    return lines
