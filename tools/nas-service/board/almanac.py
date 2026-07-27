"""老黄历 board — real almanac data via lunar_python + PIL rendering.

Two templates share one data source (get_data):
  - render_classic: 顶部日期条 + 左下黄块大字 + 右下宜忌卡片
  - render_minimal: 全屏黄底 + 居中超大农历日 + 底部宜忌一行

All text rendered via PIL 1-bit mode (zero AA, crisp on e-ink).
See docs/EPD_DESIGN_SYSTEM.md §2.5 "PIL 1bit 渲染".
"""

from datetime import datetime
from lunar_python import Solar

from board.pil_renderer import (
    pil_renderer, C_BLACK, C_WHITE, C_RED, C_YELLOW, SCREEN_W, SCREEN_H,
)


def _constellation(month: int, day: int) -> str:
    """Western zodiac sign by month/day."""
    boundaries = [(1, 20), (2, 19), (3, 21), (4, 20), (5, 21), (6, 22),
                  (7, 23), (8, 23), (9, 23), (10, 24), (11, 23), (12, 22)]
    signs = ["摩羯", "水瓶", "双鱼", "白羊", "金牛", "双子",
             "巨蟹", "狮子", "处女", "天秤", "天蝎", "射手", "摩羯"]
    idx = 0
    for i, (m, d) in enumerate(boundaries):
        if (month, day) >= (m, d):
            idx = i + 1
    return signs[idx]


def get_data(target_date: datetime = None) -> dict:
    """Build the almanac data dict for a given date (default: today).
    Shared by all templates — they pick the fields they need."""
    if target_date is None:
        target_date = datetime.now()

    solar = Solar.fromDate(target_date)
    lunar = solar.getLunar()

    weekday_cn = ["日", "一", "二", "三", "四", "五", "六"][target_date.weekday()]

    yi_list = lunar.getDayYi() or []
    ji_list = lunar.getDayJi() or []

    return {
        "solar_year": solar.getYear(),
        "solar_month": solar.getMonth(),
        "solar_day": solar.getDay(),
        "weekday_cn": weekday_cn,
        "lunar_year_ganzhi": lunar.getYearInGanZhi(),
        "lunar_month_cn": lunar.getMonthInChinese(),
        "lunar_day_cn": lunar.getDayInChinese(),
        "animal_year": lunar.getYearShengXiao(),
        "jieqi": lunar.getJieQi() or "",
        "yi": yi_list[:3],
        "ji": ji_list[:3],
        "day_ganzhi": lunar.getDayInGanZhi(),
        "chong": lunar.getDayChongDesc(),
        "sha": "煞" + lunar.getDaySha(),
        "pos_xi": lunar.getDayPositionXiDesc(),
        "pos_cai": lunar.getDayPositionCaiDesc(),
        "pos_fu": lunar.getDayPositionFuDesc(),
        "constellation": _constellation(solar.getMonth(), solar.getDay()),
    }


# ============================================================
# Shared helpers (used by both templates)
# ============================================================

def _text_w(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _draw_centered(draw, text, font, cx, y, fill):
    w = _text_w(draw, text, font)
    draw.text((cx - w // 2, y), text, font=font, fill=fill)


# ============================================================
# Template 1: Classic — full info, big focal day + 宜忌 cards
# ============================================================

def render_classic(data: dict):
    """经典版:顶部日期条 + 左下黄块大字 + 右下宜忌卡片。"""

    def layout(draw_rgb, draw_text, font):
        f48 = font(48)
        f24 = font(24)
        f16 = font(16)

        # ===== Top date strip (y=0~70) =====
        pad = 20
        left = f"{data['lunar_year_ganzhi']}年 {data['animal_year']}"
        draw_text.text((pad, 16), left, font=f24, fill=0)
        right1 = f"{data['solar_month']}.{data['solar_day']} 周{data['weekday_cn']}"
        w1 = _text_w(draw_text, right1, f24)
        draw_text.text((SCREEN_W - pad - w1, 16), right1, font=f24, fill=0)
        sub = f"{data['lunar_month_cn']}月"
        if data["jieqi"]:
            sub += f" · {data['jieqi']}"
        w2 = _text_w(draw_text, sub, f16)
        draw_text.text((SCREEN_W - pad - w2, 16 + 28), sub, font=f16, fill=0)
        draw_rgb.line([(pad, 70), (SCREEN_W - pad, 70)], fill=C_RED, width=3)

        # ===== Left focal block: yellow fill + big day (y=80~300) =====
        block_x, block_y = 0, 80
        block_w, block_h = 190, SCREEN_H - block_y
        draw_rgb.rectangle([block_x, block_y, block_x + block_w, block_y + block_h],
                           fill=C_YELLOW)
        cx = block_x + block_w // 2
        _draw_centered(draw_text, f"{data['lunar_month_cn']}月", f24,
                       cx, block_y + 20, fill=0)
        _draw_centered(draw_text, data["lunar_day_cn"], f48,
                       cx, block_y + 20 + 24 + 18, fill=0)
        _draw_centered(draw_text, data["day_ganzhi"], f24,
                       cx, block_y + block_h - 32, fill=0)

        # ===== Right cards: 宜 / 忌 =====
        card_x = 205
        card_w = SCREEN_W - card_x - 16
        card_pad = 14
        gap = 12
        card_y0 = 88
        card_h = (SCREEN_H - card_y0 - gap - 16) // 2

        draw_rgb.rectangle([card_x, card_y0, card_x + card_w, card_y0 + card_h],
                           outline=C_BLACK, width=3)
        draw_text.text((card_x + card_pad, card_y0 + 8), "宜", font=f24, fill=0)
        yi_text = " ".join(data["yi"][:2])
        draw_text.text((card_x + card_pad, card_y0 + card_h - 32),
                       yi_text, font=f24, fill=0)

        card_y1 = card_y0 + card_h + gap
        draw_rgb.rectangle([card_x, card_y1, card_x + card_w, card_y1 + card_h],
                           outline=C_RED, width=3)
        draw_text.text((card_x + card_pad, card_y1 + 8), "忌", font=f24, fill=0)
        ji_text = " ".join(data["ji"][:2])
        draw_text.text((card_x + card_pad, card_y1 + card_h - 32),
                       ji_text, font=f24, fill=0)

    return pil_renderer.render(layout)


# ============================================================
# Template 2: Minimal — fullscreen yellow, centered super-big day
# ============================================================

def render_minimal(data: dict):
    """极简版:全屏黄底 + 居中超大农历日 + 底部宜忌一行。屏保式视觉冲击。"""

    def layout(draw_rgb, draw_text, font):
        f48 = font(48)
        f24 = font(24)

        # Full-screen yellow background
        draw_rgb.rectangle([0, 0, SCREEN_W, SCREEN_H], fill=C_YELLOW)

        cx = SCREEN_W // 2

        # Month name (top, 24px black)
        _draw_centered(draw_text, f"{data['lunar_month_cn']}月", f24,
                       cx, 60, fill=0)

        # Super-big day number (focal, 48px black, vertically centered)
        _draw_centered(draw_text, data["lunar_day_cn"], f48,
                       cx, 110, fill=0)

        # Ganzhi year (below day, 24px)
        _draw_centered(draw_text, f"{data['lunar_year_ganzhi']}年 {data['animal_year']}",
                       f24, cx, 175, fill=0)

        # Bottom row: 宜 X  忌 Y (24px, one line)
        bottom_y = 250
        yi_str = f"宜 {' '.join(data['yi'][:2])}"
        ji_str = f"忌 {' '.join(data['ji'][:2])}"
        row = f"{yi_str}    {ji_str}"
        _draw_centered(draw_text, row, f24, cx, bottom_y, fill=0)

    return pil_renderer.render(layout)
