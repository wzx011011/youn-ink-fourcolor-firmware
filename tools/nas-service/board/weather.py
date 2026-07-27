"""天气看板 — Open-Meteo(免费、无需 Key、国内直连)+ PIL 渲染。

数据来自 Open-Meteo:
  GET https://api.open-meteo.com/v1/forecast?latitude=..&longitude=..&...
返回实时温度/湿度/风速 + 3 天预报。WMO weather_code 映射成中文。

默认城市:深圳(22.54, 114.06)。改城市改 CITY_LAT/CITY_LON 即可。
"""

import json
import urllib.request
import urllib.parse
from datetime import datetime

from board.pil_renderer import (
    pil_renderer, C_BLACK, C_WHITE, C_RED, C_YELLOW, SCREEN_W, SCREEN_H,
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

# WMO weather code → 中文 + 图标符号
# https://open-meteo.com/en/docs WMO Weather interpretation codes
WMO_MAP = {
    0:  ("晴", "☀"),
    1:  ("多云", "🌤"),
    2:  ("多云", "⛅"),
    3:  ("阴", "☁"),
    45: ("雾", "🌫"),
    48: ("雾凇", "🌫"),
    51: ("小雨", "🌦"),
    53: ("小雨", "🌦"),
    55: ("中雨", "🌧"),
    56: ("冻雨", "🌧"),
    57: ("冻雨", "🌧"),
    61: ("小雨", "🌦"),
    63: ("中雨", "🌧"),
    65: ("大雨", "🌧"),
    66: ("冻雨", "🌧"),
    67: ("冻雨", "🌧"),
    71: ("小雪", "🌨"),
    73: ("中雪", "❄"),
    75: ("大雪", "❄"),
    77: ("霰", "🌨"),
    80: ("阵雨", "🌦"),
    81: ("中雨", "🌧"),
    82: ("大雨", "🌧"),
    85: ("阵雪", "🌨"),
    86: ("阵雪", "🌨"),
    95: ("雷雨", "⛈"),
    96: ("雷阵雨", "⛈"),
    99: ("雷阵雨", "⛈"),
}


def _wmo(code):
    """WMO code → (中文描述, 图标)。未知代码返回默认。"""
    return WMO_MAP.get(code, ("未知", "？"))


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
    desc, icon = _wmo(wcode)

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
        d_desc, _ = _wmo(dwc[i] if i < len(dwc) else 0)
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
        "icon": icon,
        "forecast": forecast,
        "now_time": datetime.now().strftime("%H:%M"),
    }


# ============================================================
# Template: card — big current temp + forecast row
# ============================================================

def render_card(data: dict):
    """卡片版:左上大温度 + 右上天气描述 + 底部3天预报。"""

    def layout(draw_rgb, draw_text, font):
        f48 = font(48)
        f24 = font(24)
        f16 = font(16)

        pad = 20

        if not data.get("ok"):
            _draw_centered(draw_text, "暂无天气", f24,
                           SCREEN_W // 2, SCREEN_H // 2 - 16, fill=0)
            err = data.get("error", "")[:18]
            if err:
                _draw_centered(draw_text, err, f16,
                               SCREEN_W // 2, SCREEN_H // 2 + 24, fill=0)
            return

        # ===== 顶部:城市 + 时间 =====
        header = f"🌤 {data['city']}"
        draw_text.text((pad, 12), header, font=f24, fill=0)
        tm = data.get("now_time", "")
        if tm:
            w = _text_w(draw_text, tm, f16)
            draw_text.text((SCREEN_W - pad - w, 16), tm, font=f16, fill=0)
        draw_rgb.line([(pad, 46), (SCREEN_W - pad, 46)], fill=C_RED, width=2)

        # ===== 左侧:大温度(焦点)=====
        temp_str = str(data["temp"])
        draw_text.text((pad, 60), temp_str, font=f48, fill=0)
        # °C 单位(24px,跟在温度后)
        tw = _text_w(draw_text, temp_str, f48)
        draw_text.text((pad + tw + 4, 70), "°C", font=f24, fill=0)

        # ===== 右侧:天气描述 + 详情 =====
        right_x = 230
        draw_text.text((right_x, 64), data["desc"], font=f24, fill=0)
        detail1 = f"体感 {data['feels']}°"
        draw_text.text((right_x, 96), detail1, font=f16, fill=0)
        detail2 = f"湿度 {data['humidity']}%"
        draw_text.text((right_x, 116), detail2, font=f16, fill=0)
        detail3 = f"{data['wind_dir']}风 {data['wind_speed']}km/h"
        draw_text.text((right_x, 136), detail3, font=f16, fill=0)

        # ===== 底部:3天预报(横排卡片)=====
        y0 = 200
        draw_rgb.line([(pad, y0 - 8), (SCREEN_W - pad, y0 - 8)], fill=C_BLACK, width=1)
        forecast = data.get("forecast", [])
        if forecast:
            col_w = (SCREEN_W - pad * 2) // len(forecast)
            for i, f in enumerate(forecast):
                cx = pad + col_w * i + col_w // 2
                # 日期
                _draw_centered(draw_text, f["label"], f16, cx, y0, fill=0)
                # 天气
                _draw_centered(draw_text, f["desc"], f16, cx, y0 + 20, fill=0)
                # 温度 高/低
                temp_line = f"{f['high']}°/{f['low']}°"
                _draw_centered(draw_text, temp_line, f24, cx, y0 + 42, fill=0)

    return pil_renderer.render(layout)


# Shared helpers
def _text_w(draw, text, f):
    bbox = draw.textbbox((0, 0), text, font=f)
    return bbox[2] - bbox[0]


def _draw_centered(draw, text, font, cx, y, fill):
    w = _text_w(draw, text, font)
    draw.text((cx - w // 2, y), text, font=font, fill=fill)


# ============================================================
# Template 2: minimal — fullscreen yellow, super-big temp
# ============================================================

def render_minimal(data: dict):
    """极简版:全屏黄底 + 居中超大温度 + 城市名。屏保式视觉冲击。"""

    def layout(draw_rgb, draw_text, font):
        f48 = font(48)
        f24 = font(24)

        if not data.get("ok"):
            draw_rgb.rectangle([0, 0, SCREEN_W, SCREEN_H], fill=C_YELLOW)
            _draw_centered(draw_text, "暂无天气", f24,
                           SCREEN_W // 2, SCREEN_H // 2 - 12, fill=0)
            return

        draw_rgb.rectangle([0, 0, SCREEN_W, SCREEN_H], fill=C_YELLOW)
        cx = SCREEN_W // 2

        # 城市名(顶部)
        _draw_centered(draw_text, data["city"], f24, cx, 50, fill=0)
        # 天气描述
        _draw_centered(draw_text, data["desc"], f24, cx, 84, fill=0)

        # 超大温度(焦点)
        temp_str = str(data["temp"])
        _draw_centered(draw_text, temp_str, f48, cx - 16, 140, fill=0)
        tw = _text_w(draw_text, temp_str, f48)
        draw_text.text((cx - 16 + tw // 2 + 6, 150), "°", font=f24, fill=0)

        # 底部:体感 + 湿度
        bottom = f"体感{data['feels']}°  湿度{data['humidity']}%"
        _draw_centered(draw_text, bottom, f24, cx, 230, fill=0)

    return pil_renderer.render(layout)


# ============================================================
# Template 3: clock — big current time + temp
# ============================================================

def render_clock(data: dict):
    """小时强调版:顶部大时间 + 中部温度 + 底部城市/天气。"""

    def layout(draw_rgb, draw_text, font):
        f48 = font(48)
        f24 = font(24)
        f16 = font(16)
        pad = 20

        if not data.get("ok"):
            _draw_centered(draw_text, "暂无天气", f24,
                           SCREEN_W // 2, SCREEN_H // 2 - 12, fill=0)
            return

        cx = SCREEN_W // 2
        # 顶部城市
        _draw_centered(draw_text, data["city"], f24, cx, 16, fill=0)
        draw_rgb.line([(pad, 50), (SCREEN_W - pad, 50)], fill=C_RED, width=2)

        # 大时间(焦点)
        tm = data.get("now_time", "12:00")
        _draw_centered(draw_text, tm, f48, cx, 70, fill=0)

        # 温度 + 天气(中部)
        weather_line = f"{data['temp']}°C  {data['desc']}"
        _draw_centered(draw_text, weather_line, f24, cx, 140, fill=0)

        # 体感/湿度/风(下部)
        y = 190
        _draw_centered(draw_text, f"体感 {data['feels']}°C", f16, cx, y, fill=0)
        y += 24
        _draw_centered(draw_text, f"湿度 {data['humidity']}%", f16, cx, y, fill=0)
        y += 24
        wind = f"{data['wind_dir']}风 {data['wind_speed']}km/h"
        _draw_centered(draw_text, wind, f16, cx, y, fill=0)

    return pil_renderer.render(layout)


# ============================================================
# Template 4: forecast — 3-day forecast as main content
# ============================================================

def render_forecast(data: dict):
    """预报强调版:顶部小字当前 + 3天预报大卡片为主。"""

    def layout(draw_rgb, draw_text, font):
        f48 = font(48)
        f24 = font(24)
        f16 = font(16)
        pad = 20

        if not data.get("ok"):
            _draw_centered(draw_text, "暂无天气", f24,
                           SCREEN_W // 2, SCREEN_H // 2 - 12, fill=0)
            return

        # 顶部:城市 + 当前(小字)
        header = f"{data['city']}  {data['temp']}°C  {data['desc']}"
        draw_text.text((pad, 14), header, font=f24, fill=0)
        draw_rgb.line([(pad, 48), (SCREEN_W - pad, 48)], fill=C_RED, width=2)

        # 3天预报(大卡片,每个占 1/3 宽)
        forecast = data.get("forecast", [])
        if not forecast:
            return
        col_w = (SCREEN_W - pad * 2) // len(forecast)
        y0 = 70
        card_h = SCREEN_H - y0 - 16
        for i, f in enumerate(forecast):
            x0 = pad + col_w * i
            cx = x0 + col_w // 2
            # 卡片边框(交替黑/红)
            color = C_BLACK if i % 2 == 0 else C_RED
            draw_rgb.rectangle([x0 + 4, y0, x0 + col_w - 4, y0 + card_h],
                               outline=color, width=2)
            # 日期(顶部)
            _draw_centered(draw_text, f["label"], f16, cx, y0 + 12, fill=0)
            # 天气描述(中部)
            _draw_centered(draw_text, f["desc"], f16, cx, y0 + 36, fill=0)
            # 高温(大字)
            _draw_centered(draw_text, f"{f['high']}°", f48, cx, y0 + 70, fill=0)
            # 低温
            _draw_centered(draw_text, f"{f['low']}°", f24, cx, y0 + 130, fill=0)

    return pil_renderer.render(layout)


# ============================================================
# Template 5: grid — 4 data cells (temp/humidity/wind/feels)
# ============================================================

def render_grid(data: dict):
    """数据网格版:2×2 网格,每格一个大数据点。仪表盘式。"""

    def layout(draw_rgb, draw_text, font):
        f48 = font(48)
        f24 = font(24)
        f16 = font(16)
        pad = 20

        if not data.get("ok"):
            _draw_centered(draw_text, "暂无天气", f24,
                           SCREEN_W // 2, SCREEN_H // 2 - 12, fill=0)
            return

        # 顶部标题
        header = f"{data['city']} {data['desc']}"
        draw_text.text((pad, 14), header, font=f24, fill=0)
        draw_rgb.line([(pad, 48), (SCREEN_W - pad, 48)], fill=C_RED, width=2)

        # 2×2 网格
        cells = [
            ("温度", f"{data['temp']}°", "C"),
            ("体感", f"{data['feels']}°", "C"),
            ("湿度", f"{data['humidity']}", "%"),
            ("风力", f"{data['wind_speed']}", "km/h"),
        ]
        grid_y = 64
        cell_w = (SCREEN_W - pad * 2) // 2
        cell_h = (SCREEN_H - grid_y - 12) // 2
        for i, (label, value, unit) in enumerate(cells):
            row = i // 2
            col = i % 2
            x0 = pad + col * cell_w
            y0 = grid_y + row * cell_h
            cx = x0 + cell_w // 2
            # 黄底(交替)
            if (row + col) % 2 == 0:
                draw_rgb.rectangle([x0 + 4, y0 + 4, x0 + cell_w - 4, y0 + cell_h - 4],
                                   fill=C_YELLOW)
            # 标签(顶部小字)
            _draw_centered(draw_text, label, f16, cx, y0 + 16, fill=0)
            # 大数值(中部)
            _draw_centered(draw_text, value, f48, cx, y0 + 44, fill=0)
            # 单位(小字)
            vw = _text_w(draw_text, value, f48)
            draw_text.text((cx + vw // 2 + 4, y0 + 52), unit, font=f16, fill=0)

    return pil_renderer.render(layout)
