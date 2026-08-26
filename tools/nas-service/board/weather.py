"""天气看板 — Open-Meteo(免费、无需 Key、国内直连)+ PIL 渲染。

数据来自 Open-Meteo:
  GET https://api.open-meteo.com/v1/forecast?latitude=..&longitude=..&...
返回实时温度/湿度/风速 + 3 天预报。WMO weather_code 映射成中文。

默认城市:深圳(22.54, 114.06)。改城市改 CITY_LAT/CITY_LON 即可。
"""

import json
import math
import urllib.request
import urllib.parse
from datetime import datetime

from board.pil_renderer import (
    pil_renderer, C_BLACK, C_WHITE, C_RED, C_YELLOW, SCREEN_W, SCREEN_H,
    text_w, draw_centered, fit_text,
)

# ===== 城市配置(改这里换城市)=====
CITY_NAME = "深圳"
CITY_LAT = 22.54
CITY_LON = 114.06

OPEN_METEO_URL = (
    "https://api.open-meteo.com/v1/forecast"
    f"?latitude={CITY_LAT}&longitude={CITY_LON}"
    "&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
    "weather_code,wind_speed_10m,wind_direction_10m"
    "&daily=weather_code,temperature_2m_max,temperature_2m_min"
    "&timezone=Asia%2FShanghai&forecast_days=3"
)

# WMO weather code → (中文描述, 图标类别)
# 图标类别用于 draw_weather_icon 手绘矢量图标(纯色硬边,不用 emoji——
# Noto CJK 没有 emoji 字形,会渲染成豆腐块)。
# https://open-meteo.com/en/docs WMO Weather interpretation codes
WMO_MAP = {
    0:  ("晴", "sun"),
    1:  ("多云", "sun_cloud"),
    2:  ("多云", "sun_cloud"),
    3:  ("阴", "cloud"),
    45: ("雾", "fog"),
    48: ("雾凇", "fog"),
    51: ("小雨", "rain"),
    53: ("小雨", "rain"),
    55: ("中雨", "rain"),
    56: ("冻雨", "rain"),
    57: ("冻雨", "rain"),
    61: ("小雨", "rain"),
    63: ("中雨", "rain"),
    65: ("大雨", "rain"),
    66: ("冻雨", "rain"),
    67: ("冻雨", "rain"),
    71: ("小雪", "snow"),
    73: ("中雪", "snow"),
    75: ("大雪", "snow"),
    77: ("霰", "snow"),
    80: ("阵雨", "rain"),
    81: ("中雨", "rain"),
    82: ("大雨", "rain"),
    85: ("阵雪", "snow"),
    86: ("阵雪", "snow"),
    95: ("雷雨", "thunder"),
    96: ("雷阵雨", "thunder"),
    99: ("雷阵雨", "thunder"),
}


def _wmo(code):
    """WMO code → (中文描述, 图标类别)。未知代码返回默认。"""
    return WMO_MAP.get(code, ("未知", "cloud"))


# ============================================================
# Vector weather icons — pure BWRY, hard edges (PIL has no AA)
# ============================================================

def _cloud_fill(draw, x, y, w, color=C_BLACK):
    """Solid cloud silhouette, bounding box (x, y, w, w*0.6)."""
    h = int(w * 0.6)
    # base
    draw.rounded_rectangle([x, y + int(h * 0.35), x + w, y + h],
                           radius=int(h * 0.3), fill=color)
    # bumps (same fill → seamless union)
    r1 = int(w * 0.24)
    cx1, cy1 = x + int(w * 0.32), y + int(h * 0.5)
    draw.ellipse([cx1 - r1, cy1 - r1, cx1 + r1, cy1 + r1], fill=color)
    r2 = int(w * 0.28)
    cx2, cy2 = x + int(w * 0.62), y + int(h * 0.4)
    draw.ellipse([cx2 - r2, cy2 - r2, cx2 + r2, cy2 + r2], fill=color)


def _sun_fill(draw, cx, cy, r, rays=True):
    """Yellow disc + black rays."""
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=C_YELLOW)
    if rays:
        for a in range(8):
            ang = a * math.pi / 4
            x1 = cx + (r + 3) * math.cos(ang)
            y1 = cy + (r + 3) * math.sin(ang)
            x2 = cx + (r + 8) * math.cos(ang)
            y2 = cy + (r + 8) * math.sin(ang)
            draw.line([(x1, y1), (x2, y2)], fill=C_BLACK, width=2)


def draw_weather_icon(draw, kind, cx, cy, s):
    """Draw a weather glyph centered at (cx, cy), s ≈ icon size in px."""
    if kind == "sun":
        _sun_fill(draw, cx, cy, s // 3)
    elif kind == "sun_cloud":
        _sun_fill(draw, cx + s // 5, cy - s // 5, s // 4, rays=False)
        _cloud_fill(draw, cx - s // 2, cy - s // 8, int(s * 0.8))
    elif kind == "cloud":
        _cloud_fill(draw, cx - s // 2, cy - int(s * 0.3), s)
    elif kind == "fog":
        for i in range(3):
            y = cy - s // 4 + i * (s // 4)
            draw.line([(cx - s // 2, y), (cx + s // 2, y)],
                      fill=C_BLACK, width=max(2, s // 12))
    elif kind in ("rain", "snow", "thunder"):
        _cloud_fill(draw, cx - s // 2, cy - int(s * 0.35), s)
        base_y = cy + int(s * 0.28)
        if kind == "rain":
            for i in (-1, 0, 1):
                x = cx + i * (s // 4)
                draw.line([(x - 3, base_y), (x - 6, base_y + s // 5)],
                          fill=C_BLACK, width=2)
        elif kind == "snow":
            for i in (-1, 0, 1):
                x = cx + i * (s // 4)
                r = max(2, s // 14)
                draw.ellipse([x - r, base_y - r, x + r, base_y + r],
                             outline=C_BLACK, width=2)
        else:  # thunder — yellow bolt under the cloud
            pts = [(cx + 2, base_y - 6), (cx - 8, base_y + s // 6),
                   (cx - 1, base_y + s // 6), (cx - 6, base_y + s // 3 + 4),
                   (cx + 8, base_y + 2), (cx + 1, base_y + 2)]
            draw.polygon(pts, fill=C_YELLOW)


def get_data(target_date: datetime = None) -> dict:
    """Fetch current weather + 3-day forecast from Open-Meteo."""
    try:
        req = urllib.request.Request(OPEN_METEO_URL,
                                     headers={"User-Agent": "eink-board/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": f"获取天气失败: {e}", "city": CITY_NAME}

    cur = raw.get("current", {})
    daily = raw.get("daily", {})

    wcode = cur.get("weather_code", 0)
    desc, kind = _wmo(wcode)

    # 风向(角度转8方位)
    wind_deg = cur.get("wind_direction_10m", 0)
    dirs = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
    wind_dir = dirs[int((wind_deg + 22.5) // 45) % 8]

    # 3天预报
    forecast = []
    dates = daily.get("time", [])
    dmax = daily.get("temperature_2m_max", [])
    dmin = daily.get("temperature_2m_min", [])
    dwc = daily.get("weather_code", [])
    for i in range(min(3, len(dates))):
        d_desc, d_kind = _wmo(dwc[i] if i < len(dwc) else 0)
        # 日期转 周X
        try:
            dt = datetime.strptime(dates[i], "%Y-%m-%d")
            weekday = "周" + "一二三四五六日"[dt.weekday()]
            label = f"{dates[i][5:]} {weekday}"  # MM-DD 周X
        except Exception:
            label = dates[i]
        forecast.append({
            "label": label,
            "desc": d_desc,
            "kind": d_kind,
            "high": int(round(dmax[i])) if i < len(dmax) else 0,
            "low": int(round(dmin[i])) if i < len(dmin) else 0,
        })

    return {
        "ok": True,
        "city": CITY_NAME,
        "temp": int(round(cur.get("temperature_2m", 0))),
        "feels": int(round(cur.get("apparent_temperature", 0))),
        "humidity": cur.get("relative_humidity_2m", 0),
        "wind_speed": int(round(cur.get("wind_speed_10m", 0))),
        "wind_dir": wind_dir,
        "desc": desc,
        "kind": kind,
        "forecast": forecast,
        "now_time": datetime.now().strftime("%H:%M"),
    }


# ============================================================
# Template: card — big current temp + icon + forecast row
# ============================================================

def _header(draw_rgb, draw_text, left, right=""):
    """City + time header with red rule (consistent across boards)."""
    f24 = pil_renderer._font(24)
    f16 = pil_renderer._font(16)
    draw_text.text((20, 12), left, font=f24, fill=0)
    if right:
        w = text_w(draw_text, right, f16)
        draw_text.text((SCREEN_W - 20 - w, 18), right, font=f16, fill=0)
    draw_rgb.line([(20, 46), (SCREEN_W - 20, 46)], fill=C_RED, width=2)


def _empty(draw_text, font, msg="暂无天气", data=None):
    f24 = font(24)
    draw_centered(draw_text, msg, f24,
                  SCREEN_W // 2, SCREEN_H // 2 - 16, fill=0)
    err = (data or {}).get("error", "")[:18]
    if err:
        draw_centered(draw_text, err, font(16),
                      SCREEN_W // 2, SCREEN_H // 2 + 24, fill=0)


def render_card(data: dict):
    """卡片版:左上大温度 + 手绘图标 + 右侧详情 + 底部3天预报。"""

    def layout(draw_rgb, draw_text, draw_red, font):
        f48 = font(48)
        f24 = font(24)
        f16 = font(16)
        pad = 20

        if not data.get("ok"):
            _empty(draw_text, font, data=data)
            return

        _header(draw_rgb, draw_text, data["city"], data.get("now_time", ""))

        # ===== 左侧:大温度(焦点)=====
        temp_str = str(data["temp"])
        draw_text.text((pad, 64), temp_str, font=f48, fill=0)
        tw = text_w(draw_text, temp_str, f48)
        draw_text.text((pad + tw + 4, 72), "°C", font=f24, fill=0)

        # ===== 中间:天气图标 =====
        draw_weather_icon(draw_rgb, data.get("kind", "cloud"), 185, 92, 44)

        # ===== 右侧:天气描述 + 详情 =====
        right_x = 236
        draw_text.text((right_x, 64), data["desc"], font=f24, fill=0)
        draw_text.text((right_x, 100), f"体感 {data['feels']}°", font=f16, fill=0)
        draw_text.text((right_x, 122), f"湿度 {data['humidity']}%", font=f16, fill=0)
        wind = f"{data['wind_dir']}风 {data['wind_speed']}km/h"
        draw_text.text((right_x, 144), wind, font=f16, fill=0)

        # ===== 底部:3天预报(日期 + 小图标 + 高低温)=====
        y0 = 196
        draw_rgb.line([(pad, y0 - 8), (SCREEN_W - pad, y0 - 8)],
                      fill=C_BLACK, width=1)
        forecast = data.get("forecast", [])
        if forecast:
            col_w = (SCREEN_W - pad * 2) // len(forecast)
            for i, f in enumerate(forecast):
                cx = pad + col_w * i + col_w // 2
                draw_centered(draw_text, f["label"], f16, cx, y0, fill=0)
                draw_weather_icon(draw_rgb, f.get("kind", "cloud"), cx, y0 + 44, 30)
                temp_line = f"{f['high']}°/{f['low']}°"
                draw_centered(draw_text, temp_line, f24, cx, y0 + 68, fill=0)

    return pil_renderer.render(layout)


# ============================================================
# Template 2: minimal — white space + huge temp focal
# ============================================================

def render_minimal(data: dict):
    """极简版:白底呼吸感 + 超大温度焦点 + 图标点缀。"""

    def layout(draw_rgb, draw_text, draw_red, font):
        f24 = font(24)
        f16 = font(16)

        if not data.get("ok"):
            _empty(draw_text, font, data=data)
            return

        cx = SCREEN_W // 2

        # 城市 + 描述(顶部,安静的小字)
        draw_centered(draw_text, data["city"], f24, cx, 30, fill=0)
        draw_centered(draw_text, data["desc"], f16, cx, 62, fill=0)

        # 图标
        draw_weather_icon(draw_rgb, data.get("kind", "cloud"), cx, 108, 40)

        # 超大温度(焦点,80px)
        f80 = font(80)
        temp_str = str(data["temp"])
        tw = text_w(draw_text, temp_str, f80)
        deg_w = text_w(draw_text, "°", f24)
        x0 = cx - (tw + deg_w + 6) // 2
        draw_text.text((x0, 148), temp_str, font=f80, fill=0)
        draw_text.text((x0 + tw + 6, 158), "°", font=f24, fill=0)

        # 底部一行详情
        bottom = (f"体感 {data['feels']}° · 湿度 {data['humidity']}% · "
                  f"{data['wind_dir']}风 {data['wind_speed']}km/h")
        draw_centered(draw_text, bottom, f16, cx, 258, fill=0)

    return pil_renderer.render(layout)


# ============================================================
# Template 3: clock — big current time + temp
# ============================================================

def render_clock(data: dict):
    """时钟版:大时间为焦点 + 天气一行 + 详情一行。"""

    def layout(draw_rgb, draw_text, draw_red, font):
        f64 = font(64)
        f24 = font(24)
        f16 = font(16)
        pad = 20

        if not data.get("ok"):
            _empty(draw_text, font, data=data)
            return

        # 顶部:城市(左)+ 日期(右,取自预报第一天 label)
        date_label = ""
        if data.get("forecast"):
            date_label = data["forecast"][0].get("label", "")
        _header(draw_rgb, draw_text, data["city"], date_label)

        cx = SCREEN_W // 2
        # 大时间(焦点)
        tm = data.get("now_time", "12:00")
        draw_centered(draw_text, tm, f64, cx, 66, fill=0)

        # 图标 + 温度 + 天气(中部一行)
        weather_line = f"{data['temp']}°C  {data['desc']}"
        lw = text_w(draw_text, weather_line, f24)
        icon_s = 36
        total = icon_s + 12 + lw
        ix = cx - total // 2 + icon_s // 2
        draw_weather_icon(draw_rgb, data.get("kind", "cloud"), ix, 168, icon_s)
        draw_text.text((cx - total // 2 + icon_s + 12, 156), weather_line,
                       font=f24, fill=0)

        # 体感/湿度/风(底部一行)
        details = (f"体感 {data['feels']}° · 湿度 {data['humidity']}% · "
                   f"{data['wind_dir']}风 {data['wind_speed']}km/h")
        draw_centered(draw_text, details, f16, cx, 226, fill=0)

        # 底部:明日速览
        if len(data.get("forecast", [])) > 1:
            tmw = data["forecast"][1]
            line = f"明日 {tmw['desc']} {tmw['high']}°/{tmw['low']}°"
            draw_centered(draw_text, line, f16, cx, 256, fill=0)

    return pil_renderer.render(layout)


# ============================================================
# Template 4: forecast — 3-day forecast as main content
# ============================================================

def render_forecast(data: dict):
    """预报强调版:今天黄底高亮卡 + 明后天黑框卡,图标居中。"""

    def layout(draw_rgb, draw_text, draw_red, font):
        f48 = font(48)
        f24 = font(24)
        f16 = font(16)
        pad = 20

        if not data.get("ok"):
            _empty(draw_text, font, data=data)
            return

        header = f"{data['city']}  {data['temp']}°C  {data['desc']}"
        _header(draw_rgb, draw_text, header, data.get("now_time", ""))

        forecast = data.get("forecast", [])
        if not forecast:
            return
        gap = 10
        col_w = (SCREEN_W - pad * 2 - gap * (len(forecast) - 1)) // len(forecast)
        y0, card_h = 62, SCREEN_H - 62 - 12
        for i, f in enumerate(forecast):
            x0 = pad + (col_w + gap) * i
            cx = x0 + col_w // 2
            if i == 0:
                # 今天:黄底高亮
                draw_rgb.rectangle([x0, y0, x0 + col_w, y0 + card_h],
                                   fill=C_YELLOW)
            else:
                draw_rgb.rectangle([x0, y0, x0 + col_w, y0 + card_h],
                                   outline=C_BLACK, width=2)
            tag = "今天" if i == 0 else f["label"]
            draw_centered(draw_text, tag, f16, cx, y0 + 14, fill=0)
            # 今天卡多一行日期,其余卡显示天气描述
            sub = f["label"] if i == 0 else f["desc"]
            draw_centered(draw_text, sub, f16, cx, y0 + 36, fill=0)
            icon_cy = y0 + 84
            draw_weather_icon(draw_rgb, f.get("kind", "cloud"), cx, icon_cy, 40)
            # 高温(大字)+ 低温(小字)
            draw_centered(draw_text, f"{f['high']}°", f48, cx, y0 + 118, fill=0)
            draw_centered(draw_text, f"{f['low']}°", f24, cx, y0 + 182, fill=0)

    return pil_renderer.render(layout)


# ============================================================
# Template 5: grid — 4 data cells (temp/humidity/wind/feels)
# ============================================================

def render_grid(data: dict):
    """数据网格版:2×2 棋盘格,每格一个大数据点。仪表盘式。"""

    def layout(draw_rgb, draw_text, draw_red, font):
        f48 = font(48)
        f24 = font(24)
        f16 = font(16)
        pad = 20

        if not data.get("ok"):
            _empty(draw_text, font, data=data)
            return

        # 顶部:图标 + 城市/天气
        draw_weather_icon(draw_rgb, data.get("kind", "cloud"), 32, 26, 28)
        header = f"{data['city']} {data['desc']}"
        draw_text.text((56, 12), header, font=f24, fill=0)
        tm = data.get("now_time", "")
        if tm:
            w = text_w(draw_text, tm, f16)
            draw_text.text((SCREEN_W - pad - w, 18), tm, font=f16, fill=0)
        draw_rgb.line([(pad, 48), (SCREEN_W - pad, 48)], fill=C_RED, width=2)

        # 2×2 网格(对角黄底棋盘)
        cells = [
            ("温度", f"{data['temp']}°", "C"),
            ("体感", f"{data['feels']}°", "C"),
            ("湿度", f"{data['humidity']}", "%"),
            (f"{data['wind_dir']}风", f"{data['wind_speed']}", "km/h"),
        ]
        grid_y = 60
        cell_w = (SCREEN_W - pad * 2) // 2
        cell_h = (SCREEN_H - grid_y - 10) // 2
        for i, (label, value, unit) in enumerate(cells):
            row, col = divmod(i, 2)
            x0 = pad + col * cell_w
            y0 = grid_y + row * cell_h
            cx = x0 + cell_w // 2
            if (row + col) % 2 == 0:
                draw_rgb.rectangle([x0 + 4, y0 + 4, x0 + cell_w - 4,
                                    y0 + cell_h - 4], fill=C_YELLOW)
            draw_centered(draw_text, label, f16, cx, y0 + 18, fill=0)
            draw_centered(draw_text, value, f48, cx, y0 + 46, fill=0)
            vw = text_w(draw_text, value, f48)
            draw_text.text((cx + vw // 2 + 4, y0 + 56), unit, font=f16, fill=0)

    return pil_renderer.render(layout)
