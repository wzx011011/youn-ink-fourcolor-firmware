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

    # 截取前 6 条(屏幕放得下),每条限制长度避免溢出
    MAX_LEN = 16  # 24px 字号下,一行约 16 个汉字
    items = [t[:MAX_LEN] for t in items[:6]]

    return {
        "ok": True,
        "date": raw.get("date", ""),
        "items": items,
    }


# ============================================================
# Template: list — vertical title stream with dividers
# ============================================================

def render_list(data: dict):
    """列表版:顶部标题条 + 6 条新闻竖排(横线分隔)。"""

    def layout(draw_rgb, draw_text, font):
        f24 = font(24)
        f16 = font(16)

        pad = 20

        if not data.get("ok") or not data.get("items"):
            # 空态
            _draw_centered_pil(draw_text, "暂无新闻", f24,
                               SCREEN_W // 2, SCREEN_H // 2 - 20, fill=0)
            err = data.get("error", "")[:20]
            if err:
                _draw_centered_pil(draw_text, err, f16,
                                   SCREEN_W // 2, SCREEN_H // 2 + 20, fill=0)
            return

        # ===== 顶部标题条 =====
        title = "📰 今日热点"
        draw_text.text((pad, 14), title, font=f24, fill=0)
        # 右上日期
        d = data.get("date", "")
        if d and len(d) == 8:
            date_str = f"{d[4:6]}.{d[6:8]}"
            w = _text_w(draw_text, date_str, f24)
            draw_text.text((SCREEN_W - pad - w, 14), date_str, font=f24, fill=0)
        # 红色分隔线
        draw_rgb.line([(pad, 50), (SCREEN_W - pad, 50)], fill=C_RED, width=3)

        # ===== 新闻列表(竖排,横线分隔)=====
        items = data["items"]
        y = 64
        line_h = (SCREEN_H - 64 - 10) // len(items) if items else 30
        for i, title in enumerate(items):
            # 序号(红色)
            num = f"{i+1}."
            draw_text.text((pad, y + 2), num, font=f24, fill=0)
            # 标题(黑色)
            draw_text.text((pad + 36, y + 2), title, font=f24, fill=0)
            y += line_h
            # 横线分隔(除最后一条)
            if i < len(items) - 1:
                draw_rgb.line([(pad, y - 2), (SCREEN_W - pad, y - 2)],
                              fill=C_BLACK, width=1)

    return pil_renderer.render(layout)


# Shared helpers (same as almanac.py)
def _text_w(draw, text, f):
    bbox = draw.textbbox((0, 0), text, font=f)
    return bbox[2] - bbox[0]


def _draw_centered_pil(draw, text, font, cx, y, fill):
    w = _text_w(draw, text, font)
    draw.text((cx - w // 2, y), text, font=font, fill=fill)


# ============================================================
# Template 2: headline — first story big, rest small
# ============================================================

def render_headline(data: dict):
    """头条版:第一条超大(焦点),其余4条小字列表。"""

    def layout(draw_rgb, draw_text, font):
        f48 = font(48)
        f24 = font(24)
        f16 = font(16)
        pad = 20

        if not data.get("ok") or not data.get("items"):
            _draw_centered_pil(draw_text, "暂无新闻", f24,
                               SCREEN_W // 2, SCREEN_H // 2 - 12, fill=0)
            return

        items = data["items"]
        # 顶部标题
        draw_text.text((pad, 12), "📰 今日头条", font=f24, fill=0)
        draw_rgb.line([(pad, 46), (SCREEN_W - pad, 46)], fill=C_RED, width=2)

        # 第一条:黄底 + 大字(焦点)
        headline = items[0][:12]  # 大字限12字
        draw_rgb.rectangle([pad, 56, SCREEN_W - pad, 140], fill=C_YELLOW)
        draw_text.text((pad + 12, 70), "1.", font=f24, fill=0)
        # 标题可能太长换行,简化:只取12字
        draw_text.text((pad + 40, 72), headline, font=f24, fill=0)

        # 其余条:小字列表
        y = 156
        for i, title in enumerate(items[1:5], 2):
            num = f"{i}."
            draw_text.text((pad, y), num, font=f16, fill=0)
            draw_text.text((pad + 28, y), title[:18], font=f16, fill=0)
            y += 30

    return pil_renderer.render(layout)


# ============================================================
# Template 3: dual — two columns, 3 stories each
# ============================================================

def render_dual(data: dict):
    """双栏版:左右两列,各3条新闻。信息密度高。"""

    def layout(draw_rgb, draw_text, font):
        f24 = font(24)
        f16 = font(16)
        pad = 16

        if not data.get("ok") or not data.get("items"):
            _draw_centered_pil(draw_text, "暂无新闻", f24,
                               SCREEN_W // 2, SCREEN_H // 2 - 12, fill=0)
            return

        items = data["items"][:6]
        # 顶部
        draw_text.text((pad, 12), "📰 今日热点", font=f24, fill=0)
        draw_rgb.line([(pad, 46), SCREEN_W - pad, 46], fill=C_RED, width=2)

        # 中线
        mid_x = SCREEN_W // 2
        draw_rgb.line([(mid_x, 56), (mid_x, SCREEN_H - 10)], fill=C_BLACK, width=1)

        # 左右各3条
        col_w = mid_x - pad
        y0 = 60
        line_h = 70
        for i, title in enumerate(items):
            col = i // 3
            row = i % 3
            x0 = pad + col * (col_w + pad)
            y = y0 + row * line_h
            num = f"{i+1}."
            draw_text.text((x0, y), num, font=f24, fill=0)
            # 标题(每列限宽,约8字)
            draw_text.text((x0 + 32, y + 2), title[:10], font=f24, fill=0)
            # 第二行(标题剩余部分,小字)
            if len(title) > 10:
                draw_text.text((x0 + 32, y + 28), title[10:18], font=f16, fill=0)

    return pil_renderer.render(layout)


# ============================================================
# Template 4: minimal — only 3 stories, big spacing
# ============================================================

def render_minimal(data: dict):
    """极简版:只显示3条,大字大间距。呼吸感强。"""

    def layout(draw_rgb, draw_text, font):
        f48 = font(48)
        f24 = font(24)
        pad = 24

        if not data.get("ok") or not data.get("items"):
            _draw_centered_pil(draw_text, "暂无新闻", f24,
                               SCREEN_W // 2, SCREEN_H // 2 - 12, fill=0)
            return

        items = data["items"][:3]
        # 顶部标题(小)
        draw_text.text((pad, 14), "📰 热点精选", font=f24, fill=0)
        draw_rgb.line([(pad, 48), SCREEN_W - pad, 48], fill=C_RED, width=2)

        # 3条,大间距
        y = 70
        spacing = (SCREEN_H - 70 - 16) // 3
        for i, title in enumerate(items):
            # 序号(大字红色风)
            num = f"{i+1}"
            draw_text.text((pad, y), num, font=f48, fill=0)
            # 标题(24px,限12字)
            draw_text.text((pad + 60, y + 12), title[:14], font=f24, fill=0)
            y += spacing

    return pil_renderer.render(layout)


# ============================================================
# Template 5: cards — each story a bordered card
# ============================================================

def render_cards(data: dict):
    """卡片版:每条新闻一个色块卡片(交替黄/白)。"""

    def layout(draw_rgb, draw_text, font):
        f24 = font(24)
        f16 = font(16)
        pad = 16

        if not data.get("ok") or not data.get("items"):
            _draw_centered_pil(draw_text, "暂无新闻", f24,
                               SCREEN_W // 2, SCREEN_H // 2 - 12, fill=0)
            return

        items = data["items"][:5]
        # 顶部标题(极小)
        draw_text.text((pad, 8), "📰 今日热点", font=f16, fill=0)
        draw_rgb.line([(pad, 30), SCREEN_W - pad, 30], fill=C_RED, width=1)

        # 5个卡片堆叠
        y0 = 38
        avail_h = SCREEN_H - y0 - 8
        card_h = avail_h // len(items)
        for i, title in enumerate(items):
            y = y0 + i * card_h
            # 交替黄底/白底黑框
            if i % 2 == 0:
                draw_rgb.rectangle([pad, y, SCREEN_W - pad, y + card_h - 3],
                                   fill=C_YELLOW)
            else:
                draw_rgb.rectangle([pad, y, SCREEN_W - pad, y + card_h - 3],
                                   outline=C_BLACK, width=1)
            # 序号
            draw_text.text((pad + 8, y + 6), f"{i+1}.", font=f24, fill=0)
            # 标题(限16字)
            draw_text.text((pad + 40, y + 8), title[:16], font=f24, fill=0)

    return pil_renderer.render(layout)
