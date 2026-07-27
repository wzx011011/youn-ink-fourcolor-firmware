"""Board rendering pipeline.

Generates HTML/SVG pages, screenshots them via Playwright, dithers to the
4-color BWRY palette, and pushes the result to the device's Screenshot page.

Architecture (plugin-style):
    board/
        renderer.py      - Playwright screenshot engine (shared)
        almanac.py       - 老黄历 data source (lunar_python)
        templates/       - Jinja2 HTML templates
            almanac.html

Adding a new board (e.g. weather):
    1. board/weather.py  - data source returning a dict
    2. board/templates/weather.html - Jinja2 template
    3. register in board/registry.py (BOARDS dict)
No firmware or device changes needed.
"""
