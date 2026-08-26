"""Local render harness — render all 14 board templates with mock data.

Run from tools/nas-service/:  python _preview_local.py [out_dir]
Bypasses network/data sources (lunar_python etc.) by feeding representative
data dicts straight into each template's render function.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from board import almanac, news, weather, stock
from board.registry import BOARDS

ALMANAC = {
    "solar_year": 2026, "solar_month": 7, "solar_day": 28,
    "weekday_cn": "二",
    "lunar_year_ganzhi": "丙午", "lunar_month_cn": "六", "lunar_day_cn": "十五",
    "animal_year": "马", "jieqi": "大暑",
    "yi": ["嫁娶", "订盟", "纳采"], "ji": ["入宅", "开市", "动土"],
    "day_ganzhi": "癸卯", "chong": "冲鸡", "sha": "煞西",
    "pos_xi": "东南", "pos_cai": "正南", "pos_fu": "正西",
    "constellation": "狮子",
}

NEWS = {
    "ok": True, "date": "20260728",
    "items": [
        "尼安德特人是一种怎样的存在？",
        "卖鱼老婆婆说，“鱼直接捞放塑料袋里会闷死”，是真的吗？",
        "人体能接受的安全电压是不高于 36 伏吗？",
        "网友称睡前喝水溶 C 能解咖啡因，是否有科学依据？",
        "有哪些关于湖南的冷知识？",
        "为什么其他体育项目（如马拉松）没有类似足球的转会费？",
    ],
}

WEATHER = {
    "ok": True, "city": "深圳", "temp": 26, "feels": 31, "humidity": 95,
    "wind_speed": 8, "wind_dir": "东南", "desc": "阴", "kind": "cloud",
    "forecast": [
        {"label": "07-28 周二", "desc": "阵雨", "kind": "rain", "high": 30, "low": 25},
        {"label": "07-29 周三", "desc": "雷雨", "kind": "thunder", "high": 26, "low": 24},
        {"label": "07-30 周四", "desc": "晴", "kind": "sun", "high": 32, "low": 26},
    ],
    "now_time": "00:36",
}

STOCK = {
    "ok": True,
    "indices": [
        {"name": "上证指数", "price": 3858.25, "change_pct": 1.15, "up": True},
        {"name": "深证成指", "price": 14148.73, "change_pct": 2.72, "up": True},
        {"name": "创业板指", "price": 3590.79, "change_pct": -0.86, "up": False},
    ],
    "sectors": [],
    "sparkline": [3808 + (i % 7) * 3 + i * 0.6 for i in range(60)],
    "is_trading": False, "now_time": "00:36", "now_date": "07-28",
}

DATA = {"almanac": ALMANAC, "news": NEWS, "weather": WEATHER, "stock": STOCK}


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "../_previews/new")
    out.mkdir(parents=True, exist_ok=True)
    for board_id, spec in BOARDS.items():
        for tmpl_id, tmpl in spec.templates.items():
            img = tmpl.render(DATA[board_id])
            p = out / f"{board_id}_{tmpl_id}.png"
            img.save(p)
            print("OK", p)
    print(f"\nAll templates rendered to {out}")


if __name__ == "__main__":
    main()
