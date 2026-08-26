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
    text_w, draw_centered, fit_text,
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

    weekday_cn = "一二三四五六日"[target_date.weekday()]

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
# Template 1: Classic — full info, big focal day + 宜忌 cards
# ============================================================

def render_classic(data: dict):
    """经典版:顶部日期条 + 左下黄块大字 + 右下宜忌卡片。"""

    def layout(draw_rgb, draw_text, draw_red, font):
        f48 = font(48)
        f24 = font(24)
        f16 = font(16)

        # ===== Top date strip (y=0~70) =====
        pad = 20
        left = f"{data['lunar_year_ganzhi']}年 {data['animal_year']}"
        draw_text.text((pad, 12), left, font=f24, fill=0)
        sub_left = f"{data['constellation']}座"
        draw_text.text((pad, 44), sub_left, font=f16, fill=0)
        right1 = f"{data['solar_month']}.{data['solar_day']} 周{data['weekday_cn']}"
        w1 = text_w(draw_text, right1, f24)
        draw_text.text((SCREEN_W - pad - w1, 12), right1, font=f24, fill=0)
        sub = f"{data['lunar_month_cn']}月"
        if data["jieqi"]:
            sub += f" · {data['jieqi']}"
        w2 = text_w(draw_text, sub, f16)
        draw_text.text((SCREEN_W - pad - w2, 44), sub, font=f16, fill=0)
        draw_rgb.line([(pad, 70), (SCREEN_W - pad, 70)], fill=C_RED, width=3)

        # ===== Left focal block: yellow fill + big day (flush left/bottom) =====
        block_x, block_y = 0, 80
        block_w, block_h = 188, SCREEN_H - block_y
        draw_rgb.rectangle([block_x, block_y, block_x + block_w, block_y + block_h],
                           fill=C_YELLOW)
        cx = block_x + block_w // 2
        draw_centered(draw_text, f"{data['lunar_month_cn']}月", f24,
                      cx, block_y + 18, fill=0)
        draw_centered(draw_text, data["lunar_day_cn"], f48,
                      cx, block_y + 56, fill=0)
        draw_centered(draw_text, f"{data['day_ganzhi']}日", f24,
                      cx, block_y + block_h - 42, fill=0)

        # ===== Right cards: 宜 / 忌 =====
        card_x = 204
        card_w = SCREEN_W - card_x - pad
        card_pad = 12
        gap = 12
        card_y0 = 88
        card_h = (SCREEN_H - card_y0 - gap - 16) // 2
        text_w_max = card_w - card_pad * 2

        # 宜 — black border, black label
        draw_rgb.rectangle([card_x, card_y0, card_x + card_w, card_y0 + card_h],
                           outline=C_BLACK, width=2)
        draw_text.text((card_x + card_pad, card_y0 + 10), "宜", font=f24, fill=0)
        yi_text = fit_text(draw_text, " ".join(data["yi"][:2]), f24, text_w_max)
        draw_text.text((card_x + card_pad, card_y0 + card_h - 40),
                       yi_text, font=f24, fill=0)

        # 忌 — red border, red label
        card_y1 = card_y0 + card_h + gap
        draw_rgb.rectangle([card_x, card_y1, card_x + card_w, card_y1 + card_h],
                           outline=C_RED, width=2)
        draw_red.text((card_x + card_pad, card_y1 + 10), "忌", font=f24, fill=0)
        ji_text = fit_text(draw_text, " ".join(data["ji"][:2]), f24, text_w_max)
        draw_text.text((card_x + card_pad, card_y1 + card_h - 40),
                       ji_text, font=f24, fill=0)

    return pil_renderer.render(layout)


# ============================================================
# Template 2: Minimal — white space + yellow seal block focal
# ============================================================

def render_minimal(data: dict):
    """极简版:白底呼吸感 + 中央黄色印章块超大农历日 + 底部宜忌一行。"""

    def layout(draw_rgb, draw_text, draw_red, font):
        f16 = font(16)

        cx = SCREEN_W // 2

        # Top line: ganzhi year + solar date (small, quiet)
        top = (f"{data['lunar_year_ganzhi']}年 {data['animal_year']} · "
               f"{data['solar_month']}.{data['solar_day']} 周{data['weekday_cn']}")
        draw_centered(draw_text, top, f16, cx, 28, fill=0)

        # Center yellow "seal" block with the lunar day as the single focus
        block_w, block_h = 216, 148
        bx, by = cx - block_w // 2, 62
        draw_rgb.rectangle([bx, by, bx + block_w, by + block_h], fill=C_YELLOW)
        day = data["lunar_day_cn"]
        # Pick a font size that keeps 2-3 char day names inside the block
        size = {1: 88, 2: 80}.get(len(day), 60)
        fday = font(size)
        dw = text_w(draw_text, day, fday)
        # Vertically center the glyph run inside the block (font ascender ≈ size)
        draw_text.text((cx - dw // 2, by + (block_h - size) // 2 - 6),
                       day, font=fday, fill=0)

        # Below block: lunar month + day ganzhi
        sub = f"{data['lunar_month_cn']}月 · {data['day_ganzhi']}日"
        if data["jieqi"]:
            sub += f" · {data['jieqi']}"
        draw_centered(draw_text, sub, f16, cx, by + block_h + 14, fill=0)

        # Bottom row: 宜 … (black) + 忌 … (red label), centered as a unit
        f20 = font(20)
        yi_items = fit_text(draw_text, " ".join(data["yi"][:2]), f20, 150)
        ji_items = fit_text(draw_text, " ".join(data["ji"][:2]), f20, 150)
        yi_str = f"宜 {yi_items}"
        gap_s = "    "
        total = (text_w(draw_text, yi_str, f20) + text_w(draw_text, gap_s, f20)
                 + text_w(draw_text, "忌 ", f20) + text_w(draw_text, ji_items, f20))
        x = cx - total // 2
        y = 258
        draw_text.text((x, y), yi_str, font=f20, fill=0)
        x += text_w(draw_text, yi_str + gap_s, f20)
        # 忌 label red, items black
        draw_red.text((x, y), "忌", font=f20, fill=0)
        x += text_w(draw_text, "忌 ", f20)
        draw_text.text((x, y), ji_items, font=f20, fill=0)

    return pil_renderer.render(layout)
