"""Board rendering pipeline — pure PIL, zero browser.

Renders boards as pixel-exact images (1-bit text layers + RGB color
blocks) and pushes them to the device's Screenshot page as 2bpp.

Architecture:
    board/
        pil_renderer.py  - 3-layer compositor (RGB blocks / black / red)
        almanac.py       - 老黄历 (lunar_python data)
        news.py          - 热点 (知乎日报)
        weather.py       - 天气 (Open-Meteo)
        stock.py         - 股市 (腾讯指数 + 东财板块镜像 + 分时)
        scheduler.py     - background auto-push rotation
        config_store.py  - schedule persistence (/data volume)
        registry.py      - board & template registry

Adding a new template to a board: write render_<tpl>(data) in the board
module, register TemplateSpec in registry.py. Adding a board: module with
get_data() + a render fn, BoardSpec entry, PAGE_META annotation in app.py.
"""
