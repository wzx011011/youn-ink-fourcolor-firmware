"""Board registry — maps board id to its templates.

Each board has one shared data source (get_data) and multiple render
templates (layouts). Users pick a template in the detail page; all
templates consume the same data dict.

Adding a new template to an existing board:
    1. board/<name>.py: add render_<template>(data) function
    2. registry.py: add TemplateSpec entry to that board's templates dict
No other changes needed.

Adding a new board:
    1. board/<name>.py: get_data() + at least one render_<template>()
    2. registry.py: add BoardSpec entry
    3. app.py device_ping: mark the device page as type="board" with board_id
"""

from dataclasses import dataclass, field
from typing import Callable, Dict

from board import almanac
from board import news
from board import weather
from board import stock
from board import chat


@dataclass
class TemplateSpec:
    """One render variant of a board (e.g. 'classic', 'minimal')."""
    id: str           # "classic"
    label: str        # "经典版"
    render: Callable  # (data: dict) -> PIL.Image


@dataclass
class BoardSpec:
    """A board with its data source and available templates."""
    label: str                                              # human label
    get_data: Callable                                      # () -> dict
    templates: Dict[str, TemplateSpec] = field(default_factory=dict)


BOARDS: Dict[str, BoardSpec] = {
    "almanac": BoardSpec(
        label="老黄历",
        get_data=almanac.get_data,
        templates={
            "classic": TemplateSpec("classic", "经典版", almanac.render_classic),
            "minimal": TemplateSpec("minimal", "极简版", almanac.render_minimal),
        },
    ),
    "news": BoardSpec(
        label="热点新闻",
        get_data=news.get_data,
        templates={
            "list": TemplateSpec("list", "列表版", news.render_list),
            "headline": TemplateSpec("headline", "头条版", news.render_headline),
            "dual": TemplateSpec("dual", "双栏版", news.render_dual),
            "minimal": TemplateSpec("minimal", "极简版", news.render_minimal),
            "cards": TemplateSpec("cards", "卡片版", news.render_cards),
        },
    ),
    "weather": BoardSpec(
        label="天气预报",
        get_data=weather.get_data,
        templates={
            "card": TemplateSpec("card", "卡片版", weather.render_card),
            "minimal": TemplateSpec("minimal", "极简版", weather.render_minimal),
            "clock": TemplateSpec("clock", "时钟版", weather.render_clock),
            "forecast": TemplateSpec("forecast", "预报版", weather.render_forecast),
            "grid": TemplateSpec("grid", "网格版", weather.render_grid),
        },
    ),
    "stock": BoardSpec(
        label="股市行情",
        get_data=stock.get_data,
        templates={
            "dashboard": TemplateSpec("dashboard", "看盘版", stock.render_dashboard),
            "simple": TemplateSpec("simple", "简洁版", stock.render_simple),
        },
    ),
    # AI 对话:get_data() 无参调用时不发起提问(问答由 /api/board/chat
    # 携带 question 触发)。直接绑定 chat.get_data 以兼容两种签名。
    "chat": BoardSpec(
        label="AI对话",
        get_data=chat.get_data,
        templates={
            "text": TemplateSpec("text", "问答版", chat.render_text),
        },
    ),
}


def get_template(board_id: str, template_id: str) -> TemplateSpec:
    """Look up a template, falling back to the board's first template."""
    spec = BOARDS.get(board_id)
    if not spec or not spec.templates:
        raise KeyError(f"board '{board_id}' has no templates")
    if template_id in spec.templates:
        return spec.templates[template_id]
    # Fallback: first template (dict preserves insertion order in py3.7+)
    return next(iter(spec.templates.values()))
