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
    """焦点版(方案A,克制用色):上证巨型+走势线(唯一红色),次级指数纯黑箭头。"""

    def layout(draw_rgb, draw_text, font):
        f48 = font(48)
        f24 = font(24)
        f16 = font(16)
        pad = 24

        if not data.get("ok") or not data.get("indices"):
            _draw_centered(draw_text, "暂无行情", f24,
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
        cw = _text_w(draw_text, corner, f16)
        draw_text.text((SCREEN_W - pad - cw, 18), corner, font=f16, fill=0)

        # ===== 焦点:上证点位(48px)+ 涨跌(纯黑箭头+数字,不用色块)=====
        if main:
            price_str = f"{main['price']:.2f}"
            draw_text.text((pad, 56), price_str, font=f48, fill=0)
            # 涨跌:黑色箭头+数字(红块太重,改纯文字)
            pct = main["change_pct"]
            arrow = "▲" if pct >= 0 else "▼"
            sign = "+" if pct >= 0 else ""
            pct_str = f"{arrow} {sign}{pct:.2f}%"
            draw_text.text((pad, 112), pct_str, font=f24, fill=0)

        # ===== 走势线(唯一的红色装饰,视觉焦点)=====
        spark = data.get("sparkline", [])
        if spark and len(spark) > 2:
            # 走势线用红色(涨)或黑色(跌),加粗3px
            main_pct = main["change_pct"] if main else 0
            line_color = C_RED if main_pct >= 0 else C_BLACK
            _draw_sparkline(draw_rgb, spark, pad, 150, SCREEN_W - pad * 2, 50,
                            line_color)
        else:
            # 没有走势数据时,画一条提示
            draw_text.text((pad, 165), "（走势数据获取中）", font=f16, fill=0)

        # ===== 底部:次级指数(纯黑,无色块,简洁)=====
        draw_rgb.line([(pad, 214), (SCREEN_W - pad, 214)], fill=C_BLACK, width=1)
        if subs:
            col_w = (SCREEN_W - pad * 2) // len(subs)
            for i, idx in enumerate(subs):
                cx = pad + col_w * i
                # 名称(小字)
                draw_text.text((cx, 224), idx["name"], font=f16, fill=0)
                # 点位
                draw_text.text((cx, 244), f"{idx['price']:.2f}", font=f24, fill=0)
                # 涨跌:黑色箭头(无色块)
                pct = idx["change_pct"]
                arrow = "▲" if pct >= 0 else "▼"
                sign = "+" if pct >= 0 else ""
                pct_str = f"{arrow}{sign}{pct:.2f}%"
                pww = _text_w(draw_text, pct_str, f16)
                draw_text.text((cx + col_w - pww - 4, 224), pct_str, font=f16, fill=0)

    return pil_renderer.render(layout)


# ============================================================
# Template 2: simple — only 3 indices, big
# ============================================================

def render_simple(data: dict):
    """简洁版:只三大指数,大字居中。屏保式。"""

    def layout(draw_rgb, draw_text, font):
        f48 = font(48)
        f24 = font(24)
        f16 = font(16)
        pad = 20

        if not data.get("ok") or not data.get("indices"):
            _draw_centered(draw_text, "暂无行情", f24,
                           SCREEN_W // 2, SCREEN_H // 2 - 12, fill=0)
            return

        # 顶部
        draw_text.text((pad, 12), "📈 A股三大指数", font=f24, fill=0)
        w = _text_w(draw_text, data.get("now_time", ""), f16)
        draw_text.text((SCREEN_W - pad - w, 16),
                       data.get("now_time", ""), font=f16, fill=0)
        draw_rgb.line([(pad, 48), (SCREEN_W - pad, 48)], fill=C_RED, width=2)

        # 三大指数居中排列
        indices = data["indices"][:3]
        cx = SCREEN_W // 2
        y = 68
        spacing = 70
        for idx in indices:
            # 名称(24px)
            _draw_centered(draw_text, idx["name"], f24, cx, y, fill=0)
            # 点位(48px大字)
            price_str = f"{idx['price']:.2f}"
            _draw_centered(draw_text, price_str, f48, cx, y + 26, fill=0)
            # 涨跌幅(红块白字 / 纯黑)
            pct = idx["change_pct"]
            sign = "+" if pct >= 0 else ""
            pct_str = f"{sign}{pct:.2f}%"
            pww = _text_w(draw_text, pct_str, f24)
            pct_x = cx - pww // 2
            pct_y = y + 26 + 48 + 2
            if pct >= 0:
                draw_rgb.rectangle([pct_x - 6, pct_y - 2,
                                    pct_x + pww + 6, pct_y + 24], fill=C_RED)
                draw_text.text((pct_x, pct_y), pct_str, font=f24, fill=1)
            else:
                _draw_centered(draw_text, pct_str, f24, cx, pct_y, fill=0)
            y += spacing

    return pil_renderer.render(layout)


# Shared helpers
def _text_w(draw, text, f):
    bbox = draw.textbbox((0, 0), text, font=f)
    return bbox[2] - bbox[0]


def _draw_centered(draw, text, font, cx, y, fill):
    w = _text_w(draw, text, font)
    draw.text((cx - w // 2, y), text, font=font, fill=fill)
