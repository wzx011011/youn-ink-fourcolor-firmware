"""股市看板 — 腾讯财经 API(免费、无需 Key、国内直连)+ PIL 渲染。

数据:
  - 三大指数(上证/深证/创业板):点位 + 涨跌幅
  - 行业板块涨幅 Top 5

API:腾讯财经 qt.gtimg.cn(返回 GBK 编码的 ~ 分隔文本)。
休市时返回上一个交易日收盘数据(不会"暂无行情")。
"""

import json
import urllib.request
from datetime import datetime

from board.pil_renderer import (
    pil_renderer, C_BLACK, C_WHITE, C_RED, C_YELLOW, SCREEN_W, SCREEN_H,
    text_w, draw_centered,
)

# 腾讯指数接口(三大指数)
# sh000001=上证, sz399001=深证, sz399006=创业板
INDEX_URL = "https://qt.gtimg.cn/q=sh000001,sz399001,sz399006"
# 板块接口(行业板块涨幅前5)— 腾讯板块代码示例
SECTOR_CODES = [
    "bk0428",  # 银行
    "bk0447",  # 房地产
    "bk0448",  # 煤炭
    "bk0449",  # 有色金属
    "bk0733",  # 半导体
    "bk0474",  # 电子元件
    "bk0451",  # 电力
    "bk0473",  # 汽车整车
]
HEADERS = {"User-Agent": "Mozilla/5.0"}


def _fetch_gbk(url):
    """Fetch URL and decode as GBK (tencent/sina use GBK encoding)."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read().decode("gbk", errors="replace")


def _parse_tencent_line(line):
    """Parse one v_xxx="a~b~c~..." line into a dict of fields."""
    # v_sh000001="1~上证指数~000001~3858.25~..."
    if "=" not in line or "~" not in line:
        return None
    try:
        quoted = line.split("=", 1)[1].strip().strip('"').strip(";").strip()
        fields = quoted.split("~")
        return fields
    except Exception:
        return None


def get_data(target_date: datetime = None) -> dict:
    """Fetch indices + top sectors from Tencent Finance."""
    indices = []
    sectors = []

    try:
        text = _fetch_gbk(INDEX_URL)
        for line in text.split("\n"):
            fields = _parse_tencent_line(line)
            if not fields or len(fields) < 35:
                continue
            # 腾讯字段索引:
            #  [1]=名称, [3]=当前价, [4]=昨收, [31]=涨跌额, [32]=涨跌幅, [33]=涨跌幅(重)
            try:
                name = fields[1]
                price = float(fields[3])
                change_pct = float(fields[32]) if fields[32] else 0.0
                indices.append({
                    "name": name,
                    "price": price,
                    "change_pct": change_pct,
                    "up": change_pct >= 0,
                })
            except (ValueError, IndexError):
                continue
    except Exception as e:
        return {"ok": False, "error": f"获取指数失败: {e}", "indices": []}

    if not indices:
        return {"ok": False, "error": "API 返回数据为空", "indices": []}

    # 分时走势(上证)— 用于画 sparkline。用腾讯分时接口(国内直连稳定)。
    # 失败不阻塞,没走势就只显示数字。
    sparkline_points = []
    try:
        trend_url = "https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=sh000001"
        req = urllib.request.Request(trend_url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        # 格式: data.sh000001.data.data = ["0930 3808.90 7641616 ...", ...]
        trend_list = (raw.get("data", {})
                          .get("sh000001", {})
                          .get("data", {})
                          .get("data", []))
        for item in trend_list:
            parts = item.split(" ")
            if len(parts) >= 2:
                sparkline_points.append(float(parts[1]))  # [1]=价格
    except Exception:
        pass

    # 判断交易时段
    now = target_date or datetime.now()
    weekday = now.weekday()  # 0=Mon
    is_weekday = weekday < 5
    hour, minute = now.hour, now.minute
    mins = hour * 60 + minute
    in_morning = is_weekday and (9 * 60 + 30 <= mins <= 11 * 60 + 30)
    in_afternoon = is_weekday and (13 * 60 <= mins <= 15 * 60)
    is_trading = in_morning or in_afternoon

    return {
        "ok": True,
        "indices": indices,
        "sectors": sectors,
        "sparkline": sparkline_points,
        "is_trading": is_trading,
        "now_time": now.strftime("%H:%M"),
        "now_date": now.strftime("%m-%d"),
    }


# ============================================================
# Template 1: dashboard — indices + sectors (full info)
# ============================================================

def _draw_sparkline(draw_rgb, points, x, y, w, h, color):
    """Draw a 1-bit sparkline (trend line). points = list of values."""
    if len(points) < 2:
        return
    lo, hi = min(points), max(points)
    rng = (hi - lo) or 1
    step = w / (len(points) - 1)
    pts = [(x + i * step, y + h - (v - lo) / rng * h)
           for i, v in enumerate(points)]
    draw_rgb.line(pts, fill=color, width=2)
    # 末尾点强调"当前"
    ex, ey = pts[-1]
    draw_rgb.ellipse([ex - 2, ey - 2, ex + 2, ey + 2], fill=color)


def render_dashboard(data: dict):
    """焦点版:上证巨型+走势线,涨红跌黑,次级指数底栏。"""

    def layout(draw_rgb, draw_text, draw_red, font):
        f48 = font(48)
        f24 = font(24)
        f16 = font(16)
        pad = 24

        if not data.get("ok") or not data.get("indices"):
            draw_centered(draw_text, "暂无行情", f24,
                          SCREEN_W // 2, SCREEN_H // 2 - 12, fill=0)
            return

        indices = data["indices"]
        main = indices[0] if indices else None
        subs = indices[1:3] if len(indices) >= 3 else indices[1:]

        # ===== 顶部:上证名称 + 时间角落 =====
        if main:
            draw_text.text((pad, 14), main["name"], font=f24, fill=0)
        status = "●交易中" if data.get("is_trading") else "○已收盘"
        corner = f"{data.get('now_time','')} {status}"
        cw = text_w(draw_text, corner, f16)
        draw_text.text((SCREEN_W - pad - cw, 18), corner, font=f16, fill=0)

        # ===== 焦点:上证点位(48px)+ 涨跌(涨红跌黑)=====
        if main:
            price_str = f"{main['price']:.2f}"
            draw_text.text((pad, 52), price_str, font=f48, fill=0)
            pct = main["change_pct"]
            arrow = "▲" if pct >= 0 else "▼"
            sign = "+" if pct >= 0 else ""
            pct_str = f"{arrow} {sign}{pct:.2f}%"
            pct_draw = draw_red if pct >= 0 else draw_text
            pct_draw.text((pad, 110), pct_str, font=f24, fill=0)

        # ===== 走势线(涨红跌黑,视觉焦点)=====
        spark = data.get("sparkline", [])
        if spark and len(spark) > 2:
            main_pct = main["change_pct"] if main else 0
            line_color = C_RED if main_pct >= 0 else C_BLACK
            _draw_sparkline(draw_rgb, spark, pad, 148, SCREEN_W - pad * 2, 52,
                            line_color)
        else:
            draw_text.text((pad, 165), "（走势数据获取中）", font=f16, fill=0)

        # ===== 底部:次级指数(竖线分栏)=====
        draw_rgb.line([(pad, 214), (SCREEN_W - pad, 214)], fill=C_BLACK, width=1)
        if subs:
            col_w = (SCREEN_W - pad * 2) // len(subs)
            for i, idx in enumerate(subs):
                cx = pad + col_w * i
                if i > 0:
                    draw_rgb.line([(cx - 8, 224), (cx - 8, 288)],
                                  fill=C_BLACK, width=1)
                draw_text.text((cx, 226), idx["name"], font=f16, fill=0)
                draw_text.text((cx, 250), f"{idx['price']:.2f}", font=f24, fill=0)
                pct = idx["change_pct"]
                arrow = "▲" if pct >= 0 else "▼"
                sign = "+" if pct >= 0 else ""
                pct_str = f"{arrow}{sign}{pct:.2f}%"
                pww = text_w(draw_text, pct_str, f16)
                pct_draw = draw_red if pct >= 0 else draw_text
                pct_draw.text((cx + col_w - pww - 12, 230), pct_str,
                              font=f16, fill=0)

    return pil_renderer.render(layout)


# ============================================================
# Template 2: simple — 3 indices as a clean table
# ============================================================

def render_simple(data: dict):
    """简洁版:三大指数三行表(名称 | 点位 | 涨跌幅),涨红跌黑。"""

    def layout(draw_rgb, draw_text, draw_red, font):
        f24 = font(24)
        f16 = font(16)
        pad = 20

        if not data.get("ok") or not data.get("indices"):
            draw_centered(draw_text, "暂无行情", f24,
                          SCREEN_W // 2, SCREEN_H // 2 - 12, fill=0)
            return

        # 顶部:红色方块 + 标题 + 时间
        draw_rgb.rectangle([pad, 16, pad + 18, 34], fill=C_RED)
        draw_text.text((pad + 26, 12), "A股三大指数", font=f24, fill=0)
        tm = data.get("now_time", "")
        w = text_w(draw_text, tm, f16)
        draw_text.text((SCREEN_W - pad - w, 18), tm, font=f16, fill=0)
        draw_rgb.line([(pad, 46), (SCREEN_W - pad, 46)], fill=C_RED, width=2)

        # 三行表:行高均分,行内垂直居中
        indices = data["indices"][:3]
        top, bottom = 54, SCREEN_H - 8
        row_h = (bottom - top) // len(indices)
        price_right = 256     # 点位右对齐边界
        pct_right = SCREEN_W - pad
        for i, idx in enumerate(indices):
            y = top + i * row_h
            ty = y + (row_h - 28) // 2
            # 名称(左)
            draw_text.text((pad, ty), idx["name"], font=f24, fill=0)
            # 点位(右对齐到 price_right)
            price_str = f"{idx['price']:.2f}"
            pw = text_w(draw_text, price_str, f24)
            draw_text.text((price_right - pw, ty), price_str, font=f24, fill=0)
            # 涨跌幅(右对齐,涨红跌黑)
            pct = idx["change_pct"]
            arrow = "▲" if pct >= 0 else "▼"
            sign = "+" if pct >= 0 else ""
            pct_str = f"{arrow}{sign}{pct:.2f}%"
            pww = text_w(draw_text, pct_str, f24)
            pct_draw = draw_red if pct >= 0 else draw_text
            pct_draw.text((pct_right - pww, ty), pct_str, font=f24, fill=0)
            # 行分隔线
            if i < len(indices) - 1:
                draw_rgb.line([(pad, y + row_h), (SCREEN_W - pad, y + row_h)],
                              fill=C_BLACK, width=1)

    return pil_renderer.render(layout)
