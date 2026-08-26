#!/usr/bin/env python3
"""
eink-photo NAS 服务 — 图片高质量转换 + 推送到墨水屏设备

基于 epaper-dithering(gamut compression),跑在群晖 NAS 的 Docker 里。
手机浏览器访问,拖图 → 调参数 → 实时预览 → 一键推送到设备。
"""

import base64
import io
import json
import os
import threading
import time
from pathlib import Path

import urllib.request
import urllib.parse
from PIL import Image
import epaper_dithering as ed

from flask import Flask, request, jsonify, render_template, send_file

# ============================================================
# 配置(从环境变量或 config.json 读)
# ============================================================
SCREEN_WIDTH = 400
SCREEN_HEIGHT = 300
SIZE_2BPP = SCREEN_WIDTH * SCREEN_HEIGHT * 2 // 8   # 30000
SIZE_1BPP = SCREEN_WIDTH * SCREEN_HEIGHT // 8        # 15000

# 设备 IP(默认值,可被 config.json 或环境变量覆盖)
DEVICE_IP = os.environ.get("DEVICE_IP", "192.168.100.75")
UPLOAD_PORT = os.environ.get("DEVICE_PORT", "80")  # 设备 /upload 端口
HISTORY_DIR = Path(os.environ.get("HISTORY_DIR", "/data/uploads"))
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

DITHER_MODES = {
    "fs": ed.DitherMode.FLOYD_STEINBERG,
    "burkes": ed.DitherMode.BURKES,
    "atkinson": ed.DitherMode.ATKINSON,
    "stucki": ed.DitherMode.STUCKI,
    "jarvis": ed.DitherMode.JARVIS_JUDICE_NINKE,
    "sierra": ed.DitherMode.SIERRA,
    "ordered": ed.DitherMode.ORDERED,
}

app = Flask(__name__)


# ============================================================
# 图片转换核心(复用 convert_image.py 的逻辑)
# ============================================================
def fit_to_screen(img, bg=(0, 0, 0)):
    img = img.convert("RGB")
    sw, sh = img.size
    scale = min(SCREEN_WIDTH / sw, SCREEN_HEIGHT / sh)
    nw, nh = max(1, int(round(sw * scale))), max(1, int(round(sh * scale)))
    resized = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), bg)
    canvas.paste(resized, ((SCREEN_WIDTH - nw) // 2, (SCREEN_HEIGHT - nh) // 2))
    return canvas


def convert_bwry2bpp(img, mode=ed.DitherMode.FLOYD_STEINBERG,
                     exposure=1.0, saturation=1.0, shadows=0.0, highlights=0.0,
                     tone=0.0, gamut=0.5):
    dithered = ed.dither_image(
        img, ed.ColorScheme.BWRY, mode=mode, serpentine=True,
        exposure=exposure, saturation=saturation, shadows=shadows,
        highlights=highlights, tone=tone, gamut=gamut,
    )
    indexed = dithered.load()
    out = bytearray(SIZE_2BPP)
    for y in range(SCREEN_HEIGHT):
        for x in range(SCREEN_WIDTH):
            color = indexed[x, y] & 0x03
            p = y * SCREEN_WIDTH + x
            out[p >> 2] |= color << (6 - ((p & 3) * 2))
    return bytes(out), dithered


def convert_1bpp(img, mode=ed.DitherMode.FLOYD_STEINBERG,
                 exposure=1.0, saturation=1.0, shadows=0.0, highlights=0.0, tone=0.0):
    dithered = ed.dither_image(
        img, ed.ColorScheme.MONO, mode=mode, serpentine=True,
        exposure=exposure, saturation=saturation, shadows=shadows,
        highlights=highlights, tone=tone,
    )
    indexed = dithered.load()
    out = bytearray(SIZE_1BPP)
    for y in range(SCREEN_HEIGHT):
        for x in range(SCREEN_WIDTH):
            if indexed[x, y] & 0x01:
                p = y * SCREEN_WIDTH + x
                out[p >> 3] |= 1 << (7 - (p & 7))
    return bytes(out), dithered


def preview_to_png(dithered_img):
    """把 P 模式结果放大转 PNG bytes(给前端预览)。"""
    buf = io.BytesIO()
    dithered_img.convert("RGB").resize(
        (SCREEN_WIDTH * 2, SCREEN_HEIGHT * 2), Image.NEAREST
    ).save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _urlopen_retry(req_or_url, timeout=8, attempts=3):
    """urlopen with retry — the device's WiFi link drops a sizable share of
    first-connection SYNs; one blind retry converts most failures.

    NOTE: the response is intentionally returned WITHOUT its own `with`
    block — closing is owned by the caller's outer `with`. Returning it
    from inside a `with` here would hand the caller an already-closed
    object ("I/O operation on closed file" on read).
    """
    last_err = None
    for i in range(attempts):
        try:
            if isinstance(req_or_url, urllib.request.Request):
                req = urllib.request.Request(
                    req_or_url.full_url,
                    data=req_or_url.data,
                    method=req_or_url.get_method(),
                )
                for k, v in req_or_url.header_items():
                    req.add_header(k, v)
            else:
                req = req_or_url
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError:
            raise  # Server answered (4xx/5xx): don't retry blindly
        except Exception as e:
            last_err = e
            if i < attempts - 1:
                time.sleep(0.6 * (i + 1))
    raise last_err


def push_to_device(raw, fmt, title="", endpoint="/upload"):
    """Push raw bytes to the device.

    endpoint: "/upload" (photo gallery, default) or "/screenshot/set" (board).
    For /screenshot/set the label is passed via ?label= instead of ?title=.
    """
    host = DEVICE_IP
    port = f":{UPLOAD_PORT}" if UPLOAD_PORT and UPLOAD_PORT != "80" else ""
    url = f"http://{host}{port}{endpoint}?format={urllib.parse.quote(fmt)}"
    if title:
        if endpoint == "/screenshot/set":
            url += "&label=" + urllib.parse.quote(title)
        else:
            url += "&title=" + urllib.parse.quote(title)
    req = urllib.request.Request(url, data=raw, method="POST")
    req.add_header("Content-Type", "application/octet-stream")
    try:
        with _urlopen_retry(req, timeout=30) as resp:
            return True, resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return False, str(e)


def device_url(path):
    """构造设备 URL,带端口。"""
    port = f":{UPLOAD_PORT}" if UPLOAD_PORT and UPLOAD_PORT != "80" else ""
    return f"http://{DEVICE_IP}{port}{path}"


# Page metadata merged onto the device-reported page list.
# type: "raw" = firmware-native UI (tap switches device page immediately)
#       "board" = NAS-rendered board (tap opens template detail page)
PAGE_META = {
    "gallery":      {"name": "相册",     "type": "raw"},
    "weather":      {"name": "天气",     "type": "board", "board_id": "weather"},
    "calendar":     {"name": "日历",     "type": "raw"},
    "news":         {"name": "热点",     "type": "board", "board_id": "news"},
    "ebook":        {"name": "电子书",   "type": "raw"},
    "lifebar":      {"name": "人生进度", "type": "raw"},
    "yearprogress": {"name": "年度进度", "type": "raw"},
    "almanac":      {"name": "老黄历",   "type": "board", "board_id": "almanac"},
    "screenshot":   {"name": "看板",     "type": "raw"},
    "log":          {"name": "日志",     "type": "raw"},
    "settings":     {"name": "设置",     "type": "raw"},
}


def device_ping():
    """Probe device reachability + remote-control support.

    Zero side effects: only GET endpoints are touched. Reads the real
    page list from /page/list when available (newer firmware) instead of
    a hardcoded copy — old firmware without the route reports
    page_control=False. Returns (reachable, supported, pages_json).
    """
    try:
        with _urlopen_retry(device_url("/status"), timeout=6) as resp:
            resp.read()
    except Exception:
        return False, False, "[]"

    try:
        with _urlopen_retry(device_url("/page/list"), timeout=6) as resp:
            device_pages = json.loads(resp.read().decode("utf-8"))
    except Exception:
        # Older firmware: no /page/list route → remote switching unsupported
        return True, False, "[]"

    # Merge NAS-side metadata (type/board_id) onto device-authored entries
    pages = []
    for p in device_pages:
        meta = PAGE_META.get(p.get("id", ""), {})
        pages.append({
            "id": p.get("id"),
            "name": meta.get("name") or p.get("name") or p.get("id"),
            "active": p.get("active", False),
            "type": meta.get("type", "raw"),
            **({"board_id": meta["board_id"]} if "board_id" in meta else {}),
        })
    return True, bool(pages), json.dumps(pages)


def device_switch_page(page_id):
    """转发 /page/show 给设备。"""
    body = json.dumps({"page": page_id}).encode("utf-8")
    req = urllib.request.Request(
        device_url("/page/show"), data=body, method="POST"
    )
    req.add_header("Content-Type", "application/json")
    try:
        with _urlopen_retry(req, timeout=8) as resp:
            return True, resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return False, str(e)


# ============================================================
# 路由
# ============================================================
@app.route("/")
def index():
    return render_template("index.html", device_ip=DEVICE_IP)


@app.route("/api/convert", methods=["POST"])
def api_convert():
    """接收图片 + 参数,返回预览 PNG + raw base64。"""
    f = request.files.get("image")
    if not f:
        return jsonify(error="缺少 image"), 400
    try:
        img = Image.open(io.BytesIO(f.read()))
    except Exception as e:
        return jsonify(error=f"图片读不了: {e}"), 400

    fmt = request.form.get("format", "bwry2bpp")
    mode_key = request.form.get("dither", "fs")
    mode = DITHER_MODES.get(mode_key, ed.DitherMode.FLOYD_STEINBERG)
    bg = (255, 255, 255) if request.form.get("bg", "black") == "white" else (0, 0, 0)

    def fval(name, default):
        v = request.form.get(name)
        try:
            return float(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    common = dict(
        mode=mode,
        exposure=fval("exposure", 1.0),
        saturation=fval("saturation", 1.0),
        shadows=fval("shadows", 0.0),
        highlights=fval("highlights", 0.0),
        tone=fval("tone", 0.0),
    )

    fitted = fit_to_screen(img, bg=bg)
    try:
        if fmt == "1bpp":
            raw, dithered = convert_1bpp(fitted, **common)
        else:
            gamut = fval("gamut", 0.5)
            raw, dithered = convert_bwry2bpp(fitted, gamut=gamut, **common)
    except Exception as e:
        return jsonify(error=f"转换失败: {e}"), 500

    preview_png = preview_to_png(dithered)

    # 存历史(可选)
    ts = int(time.time())
    with open(HISTORY_DIR / f"{ts}.bin", "wb") as fp:
        fp.write(raw)
    with open(HISTORY_DIR / f"{ts}.png", "wb") as fp:
        fp.write(preview_png)

    return jsonify(
        ok=True,
        size=len(raw),
        format=fmt,
        preview="data:image/png;base64," + base64.b64encode(preview_png).decode(),
        raw_b64=base64.b64encode(raw).decode(),
    )


@app.route("/api/push", methods=["POST"])
def api_push():
    """推送 raw(从 base64)到设备。"""
    data = request.get_json(force=True, silent=True) or {}
    raw_b64 = data.get("raw_b64", "")
    fmt = data.get("format", "bwry2bpp")
    title = data.get("title", "")
    try:
        raw = base64.b64decode(raw_b64)
    except Exception:
        return jsonify(error="raw_b64 无效"), 400
    ok, msg = push_to_device(raw, fmt, title)
    return jsonify(ok=ok, message=msg, device=f"{DEVICE_IP}:{UPLOAD_PORT}")


@app.route("/api/bing", methods=["GET"])
def api_bing():
    """抓 Bing 每日壁纸,返回图片。"""
    try:
        ctx_req = urllib.request.Request(
            "https://cn.bing.com/HPImageArchive.aspx?format=js&idx=0&n=1&mkt=zh-CN",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(ctx_req, context=ctx, timeout=15) as resp:
            meta = json.loads(resp.read().decode("utf-8"))
        img_url = "https://cn.bing.com" + meta["images"][0]["url"]
        copyright = meta["images"][0].get("copyright", "")
        req2 = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req2, context=ctx, timeout=30) as resp:
            img_bytes = resp.read()
        buf = io.BytesIO(img_bytes)
        b64 = base64.b64encode(img_bytes).decode()
        return jsonify(ok=True, image_b64="data:image/jpeg;base64," + b64,
                       copyright=copyright, name="bing_daily.jpg")
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    global DEVICE_IP, UPLOAD_PORT
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        if data.get("device_ip"):
            DEVICE_IP = data["device_ip"]
        if data.get("device_port"):
            UPLOAD_PORT = data["device_port"]
        return jsonify(ok=True, device_ip=DEVICE_IP, device_port=UPLOAD_PORT)
    return jsonify(device_ip=DEVICE_IP, device_port=UPLOAD_PORT)


@app.route("/api/device/status", methods=["GET"])
def api_device_status():
    """探测设备可达性 + 是否支持页面控制。"""
    reachable, page_ctrl, page_list = device_ping()
    return jsonify(
        reachable=reachable,
        device=f"{DEVICE_IP}:{UPLOAD_PORT}",
        page_control=page_ctrl,
        pages=json.loads(page_list) if page_list else [],
    )


@app.route("/api/device/switch_page", methods=["POST"])
def api_device_switch_page():
    """转发 /page/show 给设备(切页面)。"""
    data = request.get_json(force=True, silent=True) or {}
    page_id = data.get("page", "")
    if not page_id:
        return jsonify(ok=False, error="缺少 page"), 400
    ok, msg = device_switch_page(page_id)
    return jsonify(ok=ok, message=msg, page=page_id, device=f"{DEVICE_IP}:{UPLOAD_PORT}")


@app.route("/api/device/lifebar_birth", methods=["POST"])
def api_device_lifebar_birth():
    """配置人生进度出生日期。转发给设备存 NVS。Body: {y,m,d}"""
    data = request.get_json(force=True, silent=True) or {}
    y, m, d = data.get("y"), data.get("m"), data.get("d")
    if not all(isinstance(v, int) for v in (y, m, d)):
        return jsonify(ok=False, error="y/m/d 需为整数"), 400
    url = device_url("/lifebar/birth")
    body = json.dumps({"y": y, "m": m, "d": d}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with _urlopen_retry(req, timeout=10) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace")
        ok = '"success":true' in resp_body
        # Surface the device's own error field (e.g. invalid_date) if present
        dev_err = None
        if not ok:
            try:
                dev_err = json.loads(resp_body).get("error") or resp_body
            except Exception:
                dev_err = resp_body
        return jsonify(ok=ok, device=f"{DEVICE_IP}:{UPLOAD_PORT}",
                       error=dev_err)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 502


# ============================================================
# Board pipeline: PIL 1-bit render → dither → push (no browser)
# ============================================================
from board.registry import BOARDS, get_template
from board import config_store
from board.scheduler import scheduler


# Short-lived data cache: the detail page fires N template previews at once
# (N = thumbnails) and each would otherwise re-hit the remote weather/news/
# stock APIs. 60s TTL keeps previews fresh enough while collapsing that
# burst to one upstream call per board.
_DATA_CACHE = {}
_DATA_TTL = 60


def _get_data_cached(spec, board_id):
    now = time.time()
    hit = _DATA_CACHE.get(board_id)
    if hit and now - hit[0] < _DATA_TTL:
        return hit[1]
    data = spec.get_data()
    _DATA_CACHE[board_id] = (now, data)
    return data


def render_board(board_id: str, template_id: str = "", push: bool = False,
                 auto_switch: bool = True, data_override=None):
    """Render a board template to 2bpp bytes. Optionally push to device.

    template_id: which template to render (e.g. 'classic', 'minimal').
                 Empty/unknown falls back to the board's first template.
    data_override: extra kwargs for the board's get_data (e.g. chat's
                 question). When provided the cache is bypassed.
    auto_switch: if True (and push True), switch the device to the board page.
    Returns dict: {ok, label, template, preview_b64, raw_b64, pushed}.
    """
    spec = BOARDS.get(board_id)
    if not spec:
        return {"ok": False, "error": f"unknown board: {board_id}"}
    if not spec.templates:
        return {"ok": False, "error": f"board '{board_id}' has no templates"}

    # Resolve template (fallback to first)
    try:
        tmpl = get_template(board_id, template_id)
    except KeyError as e:
        return {"ok": False, "error": str(e)}

    # 1. Data (shared across all templates; cached to avoid API bursts)
    try:
        if data_override is not None:
            data = spec.get_data(**data_override)
        else:
            data = _get_data_cached(spec, board_id)
    except Exception as e:
        return {"ok": False, "error": f"data source failed: {e}"}

    # 2. Render via the selected template's render function (PIL 1-bit, zero AA)
    try:
        img = tmpl.render(data)
    except Exception as e:
        return {"ok": False, "error": f"render failed: {e}"}

    # 3. Fit + dither to BWRY 2bpp. ATKINSON keeps glyph edges clean.
    img = fit_to_screen(img, bg="white")
    raw, dithered = convert_bwry2bpp(img, mode=ed.DitherMode.ATKINSON, gamut=0.5)
    preview_png = preview_to_png(dithered)

    preview_b64 = base64.b64encode(preview_png).decode("ascii")
    raw_b64 = base64.b64encode(raw).decode("ascii")

    pushed = False
    push_msg = ""
    if push:
        pushed, push_msg = push_to_device(raw, "bwry2bpp",
                                          title=spec.label,
                                          endpoint="/screenshot/set")
        if pushed and auto_switch:
            device_switch_page("screenshot")
        # Notify scheduler so it doesn't immediately re-push the same board
        if pushed:
            scheduler.mark_pushed(board_id)

    return {
        "ok": True,
        "board": board_id,
        "template": tmpl.id,
        "label": spec.label,
        "preview": f"data:image/png;base64,{preview_b64}",
        "raw_b64": raw_b64,
        "pushed": pushed,
        "push_msg": push_msg,
    }


@app.route("/api/board/preview", methods=["GET"])
def api_board_preview():
    """Render a board template and return the preview PNG."""
    board_id = request.args.get("board", "almanac")
    template_id = request.args.get("template", "")
    result = render_board(board_id, template_id=template_id, push=False)
    if not result.get("ok"):
        return jsonify(result), 400
    preview_b64 = result["preview"].split(",", 1)[1]
    resp = send_file(io.BytesIO(base64.b64decode(preview_b64)),
                     mimetype="image/png")
    # Same template repeats across thumbnail + main preview; let the
    # browser reuse it (the front-end still busts with &_t for refresh).
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp


@app.route("/api/board/render", methods=["POST"])
def api_board_render():
    """Render a board template. Body JSON: {board, template, push, auto_switch}."""
    data = request.get_json(force=True, silent=True) or {}
    board_id = data.get("board", "almanac")
    template_id = data.get("template", "")
    push = bool(data.get("push", False))
    auto_switch = bool(data.get("auto_switch", True))
    result = render_board(board_id, template_id=template_id,
                          push=push, auto_switch=auto_switch)
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


@app.route("/detail")
def detail_page():
    """Board detail page — shows template selection + preview + push."""
    board = request.args.get("board", "almanac")
    spec = BOARDS.get(board)
    if not spec:
        # Unknown board id → 404 instead of rendering a page with raw query
        # echoed into its JS (defense-in-depth against script injection).
        from flask import abort
        abort(404)
    return render_template("detail.html", board=board,
                           board_label=spec.label)


@app.route("/api/board/list", methods=["GET"])
def api_board_list():
    """List available boards and their templates for the web UI."""
    boards = []
    for bid, spec in BOARDS.items():
        boards.append({
            "id": bid,
            "label": spec.label,
            "templates": [
                {"id": t.id, "label": t.label}
                for t in spec.templates.values()
            ],
        })
    return jsonify(boards=boards)


@app.route("/api/board/chat", methods=["POST"])
def api_board_chat():
    """Ask dify, render the answer, push to the device.
    Body: {question: "...", auto_switch: bool} """
    from board import chat as chat_mod
    data = request.get_json(force=True, silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify(ok=False, error="请输入问题"), 400
    # Don't waste an e-ink refresh on a guidance screen — reject early so
    # the phone can prompt for the API key instead.
    if not chat_mod._api_key():
        return jsonify(ok=False,
                       error="未配置 DIFY_API_KEY,请在「设置」页粘贴 dify 应用 Key",
                       need_key=True), 400
    auto_switch = bool(data.get("auto_switch", True))
    result = render_board("chat", template_id="text", push=True,
                          auto_switch=auto_switch,
                          data_override={"question": question})
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


@app.route("/api/dify_key", methods=["GET", "POST"])
def api_dify_key():
    """Get/set the dify app API key (persisted in the /data config)."""
    from board import chat as chat_mod
    if request.method == "GET":
        key = chat_mod._api_key()
        return jsonify(configured=bool(key))
    data = request.get_json(force=True, silent=True) or {}
    key = (data.get("key") or "").strip()
    try:
        chat_mod.set_api_key(key)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500
    return jsonify(ok=True, configured=bool(key))


@app.route("/api/schedule", methods=["GET"])
def api_schedule_get():
    """Get all board push schedules."""
    schedules = config_store.load()
    # Show every registered board in the UI even before its first save;
    # unconfigured boards default to disabled and are simply ignored by
    # the scheduler until the user enables them.
    for bid, spec in BOARDS.items():
        if bid not in schedules:
            first = next(iter(spec.templates.values()), None)
            schedules[bid] = {"enabled": False,
                              "template": first.id if first else "",
                              "interval_min": 60, "smart": False}
    for bid, cfg in schedules.items():
        spec = BOARDS.get(bid)
        cfg["label"] = spec.label if spec else bid
        cfg["templates"] = ([
            {"id": t.id, "label": t.label}
            for t in spec.templates.values()
        ] if spec else [])
    return jsonify(schedules=schedules)


@app.route("/api/schedule", methods=["POST"])
def api_schedule_set():
    """Update one board's schedule. Body: {board, enabled, template, interval_min, smart}."""
    data = request.get_json(force=True, silent=True) or {}
    board_id = data.get("board", "")
    if not board_id:
        return jsonify(ok=False, error="缺少 board"), 400
    updates = {}
    for key in ("enabled", "template", "interval_min", "smart"):
        if key in data:
            updates[key] = data[key]
    try:
        updated = config_store.update_board(board_id, **updates)
        return jsonify(ok=True, board=board_id, schedule=updated)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


# ===== Start the background scheduler =====
# Inject render_board into scheduler (avoids circular import at module load).
# Gunicorn with 1 worker = scheduler runs once.
scheduler.configure(render_board)
scheduler.start()


if __name__ == "__main__":
    # 生产用 gunicorn,Dockerfile 里配
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8096)), debug=False)
