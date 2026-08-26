"""热点新闻 board — 知乎日报数据源 + PIL 渲染。

数据来自知乎日报 API(免费、免 Key、国内直连):
  GET https://news-at.zhihu.com/api/4/news/latest
返回当天 topStories(5 条热问)+ stories(几篇文章)。

渲染:list 模板(竖排标题流,每条一行,横线分隔)。
"""

import json
import urllib.request
from datetime import datetime

from board.pil_renderer import (
    pil_renderer, C_BLACK, C_WHITE, C_RED, C_YELLOW, SCREEN_W, SCREEN_H,
    text_w, draw_centered, fit_text, wrap_text,
)

ZHIHU_API = "https://news-at.zhihu.com/api/4/news/latest"


def get_data(target_date: datetime = None) -> dict:
    """Fetch today's top stories from Zhihu Daily."""
    try:
        req = urllib.request.Request(ZHIHU_API, headers={"User-Agent": "eink-board/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {
            "ok": False,
            "error": f"获取新闻失败: {e}",
            "date": "",
            "items": [],
        }

    # 优先 topStories(热问),不够再用 stories 补
    items = []
    for s in raw.get("top_stories", []):
        items.append(s.get("title", "").strip())
    for s in raw.get("stories", []):
        title = s.get("title", "").strip()
        if title and title not in items:
            items.append(title)

    # 取前 8 条(渲染层按像素宽度截断,这里不再按字符数硬切)
    items = [t for t in items[:8] if t]

    return {
        "ok": True,
        "date": raw.get("date", ""),
        "items": items,
    }


# ============================================================
# Shared helpers
# ============================================================

PAD = 20


def _header(draw_rgb, draw_text, title, right="", y=14):
    """Common header: red square chip + title + optional right text + red rule."""
    f24 = pil_renderer._font(24)
    f16 = pil_renderer._font(16)
    draw_rgb.rectangle([PAD, y + 4, PAD + 18, y + 22], fill=C_RED)
    draw_text.text((PAD + 26, y), title, font=f24, fill=0)
    if right:
        w = text_w(draw_text, right, f16)
        draw_text.text((SCREEN_W - PAD - w, y + 6), right, font=f16, fill=0)
    draw_rgb.line([(PAD, y + 34), (SCREEN_W - PAD, y + 34)], fill=C_RED, width=2)


def _date_str(data):
    d = data.get("date", "")
    return f"{d[4:6]}.{d[6:8]}" if d and len(d) == 8 else ""


def _empty(draw_text, font, data):
    f24 = font(24)
    f16 = font(16)
    draw_centered(draw_text, "暂无新闻", f24,
                  SCREEN_W // 2, SCREEN_H // 2 - 20, fill=0)
    err = data.get("error", "")[:20]
    if err:
        draw_centered(draw_text, err, f16,
                      SCREEN_W // 2, SCREEN_H // 2 + 20, fill=0)


# ============================================================
# Template: list — vertical title stream with dividers
# ============================================================

def render_list(data: dict):
    """列表版:顶部标题条 + 新闻竖排(横线分隔,像素级截断)。"""

    def layout(draw_rgb, draw_text, draw_red, font):
        f24 = font(24)

        if not data.get("ok") or not data.get("items"):
            _empty(draw_text, font, data)
            return

        _header(draw_rgb, draw_text, "今日热点", _date_str(data))

        items = data["items"][:6]
        top, bottom = 62, SCREEN_H - 8
        row_h = (bottom - top) // len(items)
        title_x = PAD + 38
        title_w = SCREEN_W - PAD - title_x
        for i, title in enumerate(items):
            row_y = top + i * row_h
            ty = row_y + (row_h - 28) // 2  # 28 ≈ f24 glyph height
            draw_red.text((PAD, ty), f"{i+1}.", font=f24, fill=0)
            draw_text.text((title_x, ty),
                           fit_text(draw_text, title, f24, title_w),
                           font=f24, fill=0)
            if i < len(items) - 1:
                draw_rgb.line([(PAD, row_y + row_h), (SCREEN_W - PAD, row_y + row_h)],
                              fill=C_BLACK, width=1)

    return pil_renderer.render(layout)


# ============================================================
# Template 2: headline — first story big, rest small
# ============================================================

def render_headline(data: dict):
    """头条版:第一条黄底大字(焦点,可两行),其余小字列表。"""

    def layout(draw_rgb, draw_text, draw_red, font):
        f24 = font(24)
        f16 = font(16)

        if not data.get("ok") or not data.get("items"):
            _empty(draw_text, font, data)
            return

        _header(draw_rgb, draw_text, "今日头条", _date_str(data))

        items = data["items"]
        # 头条:黄底块,标题按像素换行(最多2行),块高随内容
        head_lines = wrap_text(draw_text, items[0], f24,
                               SCREEN_W - PAD * 2 - 28, max_lines=2)
        block_y = 58
        line_h = 30
        block_h = 16 + line_h * len(head_lines)
        draw_rgb.rectangle([PAD, block_y, SCREEN_W - PAD, block_y + block_h],
                           fill=C_YELLOW)
        for j, ln in enumerate(head_lines):
            draw_text.text((PAD + 14, block_y + 8 + j * line_h), ln,
                           font=f24, fill=0)

        # 其余条:红色序号 + 小字列表
        y = block_y + block_h + 14
        avail = SCREEN_H - 8 - y
        rest = items[1:5]
        if not rest:
            return
        row_h = min(30, avail // len(rest))
        for i, title in enumerate(rest, 2):
            draw_red.text((PAD, y), f"{i}.", font=f16, fill=0)
            draw_text.text((PAD + 26, y),
                           fit_text(draw_text, title, f16,
                                    SCREEN_W - PAD * 2 - 26),
                           font=f16, fill=0)
            y += row_h

    return pil_renderer.render(layout)


# ============================================================
# Template 3: dual — two columns, 3 stories each
# ============================================================

def render_dual(data: dict):
    """双栏版:左右两列各3条,标题栏宽内换行,互不越界。"""

    def layout(draw_rgb, draw_text, draw_red, font):
        f24 = font(24)
        f20 = font(20)
        f16 = font(16)

        if not data.get("ok") or not data.get("items"):
            _empty(draw_text, font, data)
            return

        _header(draw_rgb, draw_text, "今日热点", _date_str(data))

        items = data["items"][:6]
        mid_x = SCREEN_W // 2
        draw_rgb.line([(mid_x, 60), (mid_x, SCREEN_H - 10)],
                      fill=C_BLACK, width=1)

        col_w = mid_x - PAD - 12          # 168px per column
        cols_x = [PAD, mid_x + 12]
        top = 64
        row_h = (SCREEN_H - 10 - top) // 3
        for i, title in enumerate(items):
            col, row = divmod(i, 3)
            x0 = cols_x[col]
            y = top + row * row_h
            draw_red.text((x0, y + 2), f"{i+1}", font=f20, fill=0)
            lines = wrap_text(draw_text, title, f16, col_w - 30, max_lines=3)
            for j, ln in enumerate(lines):
                draw_text.text((x0 + 30, y + 4 + j * 20), ln, font=f16, fill=0)

    return pil_renderer.render(layout)


# ============================================================
# Template 4: minimal — only 3 stories, big spacing
# ============================================================

def render_minimal(data: dict):
    """极简版:3 条,红色大序号 + 两行标题,呼吸感强。"""

    def layout(draw_rgb, draw_text, draw_red, font):
        f48 = font(48)
        f24 = font(24)

        if not data.get("ok") or not data.get("items"):
            _empty(draw_text, font, data)
            return

        _header(draw_rgb, draw_text, "热点精选")

        items = data["items"][:3]
        top = 66
        row_h = (SCREEN_H - 12 - top) // 3
        title_x = PAD + 64
        title_w = SCREEN_W - PAD - title_x
        for i, title in enumerate(items):
            y = top + i * row_h
            draw_red.text((PAD, y + 6), f"{i+1}", font=f48, fill=0)
            lines = wrap_text(draw_text, title, f24, title_w, max_lines=2)
            ty = y + (row_h - 32 * len(lines)) // 2 + 2
            for j, ln in enumerate(lines):
                draw_text.text((title_x, ty + j * 32), ln, font=f24, fill=0)

    return pil_renderer.render(layout)


# ============================================================
# Template 5: cards — #1 yellow highlight, rest bordered
# ============================================================

def render_cards(data: dict):
    """卡片版:头条黄色高亮卡,其余白底黑框卡。"""

    def layout(draw_rgb, draw_text, draw_red, font):
        f24 = font(24)
        f20 = font(20)
        f16 = font(16)

        if not data.get("ok") or not data.get("items"):
            _empty(draw_text, font, data)
            return

        _header(draw_rgb, draw_text, "今日热点", _date_str(data))

        items = data["items"][:5]
        top = 56
        gap = 6
        card_h = (SCREEN_H - 8 - top - gap * (len(items) - 1)) // len(items)
        for i, title in enumerate(items):
            y = top + i * (card_h + gap)
            x0, x1 = PAD, SCREEN_W - PAD
            first = (i == 0)
            if first:
                draw_rgb.rectangle([x0, y, x1, y + card_h], fill=C_YELLOW)
            else:
                draw_rgb.rectangle([x0, y, x1, y + card_h],
                                   outline=C_BLACK, width=1)
            fnt = f24 if first else f20
            gh = 28 if first else 24
            ty = y + (card_h - gh) // 2
            num_draw = draw_text if first else draw_red
            num_draw.text((x0 + 12, ty), f"{i+1}.", font=fnt, fill=0)
            draw_text.text((x0 + 46, ty),
                           fit_text(draw_text, title, fnt, x1 - x0 - 60),
                           font=fnt, fill=0)

    return pil_renderer.render(layout)
