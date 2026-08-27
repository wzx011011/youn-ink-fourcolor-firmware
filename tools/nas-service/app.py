#!/usr/bin/env python3
"""
eink-photo NAS 服务 — 图片高质量转换 + 推送到墨水屏设备

基于 epaper-dithering(gamut compression),跑在群晖 NAS 的 Docker 里。
手机浏览器访问,拖图 → 调参数 → 实时预览 → 一键推送到设备。
"""

import base64
import hmac
import io
import ipaddress
import json
import logging
import os
import re
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
# 设备侧令牌(固件 LAN 鉴权):环境变量或设置页下发,仅存内存。
# 固件在 NVS 生成 8 位 hex;LAN 模式下设备要求 POST/DELETE 及 /photos 等
# 请求带 X-Device-Token 头(GET / 与 GET /status 保持开放)
DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN", "").strip()
_DEVICE_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{8,64}$")
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
# 上传保护:超大 body 会拖垮单 worker 的 NAS 容器
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20MB


# ============================================================
# 鉴权(可选):设置 NAS_SERVICE_TOKEN 后,/api/* 全部要求
# X-Auth-Token 头(含 GET,防止 AI Key 等敏感配置被读取)
# ============================================================
_AUTH_TOKEN = os.environ.get("NAS_SERVICE_TOKEN", "").strip()

if _AUTH_TOKEN:
    @app.before_request
    def _require_token():
        if request.path.startswith("/api/"):
            sent = request.headers.get("X-Auth-Token") or ""
            if not hmac.compare_digest(sent, _AUTH_TOKEN):
                return jsonify(error="unauthorized: X-Auth-Token 缺失或不匹配"), 401
else:
    logging.getLogger(__name__).warning(
        "NAS_SERVICE_TOKEN 未设置,API 处于无鉴权状态"
        "(建议在 docker-compose.yml 的 environment 里配置)")


def _json_body():
    """只在 Content-Type 为 application/json 时解析 body(CSRF 加固)。

    跨站表单/无 Content-Type 的 POST 会被拒,配合 token 双保险。
    """
    ctype = (request.content_type or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        return {}
    return request.get_json(silent=True) or {}


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


HISTORY_KEEP = 200


def _prune_history(keep=HISTORY_KEEP):
    """转换历史只保留最近 keep 对(.bin/.png),防止 /data 无限增长。"""
    try:
        bins = sorted(HISTORY_DIR.glob("*.bin"))
        for old in bins[:-keep] if keep else bins:
            old.unlink(missing_ok=True)
            (HISTORY_DIR / f"{old.stem}.png").unlink(missing_ok=True)
    except OSError as e:
        logging.getLogger(__name__).warning("history prune failed: %s", e)


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
    req = device_request(url, data=raw, method="POST")
    req.add_header("Content-Type", "application/octet-stream")
    # /upload 非幂等(失败重试会产生重复照片),不重试;其余端点保持 3 次
    attempts = 1 if endpoint == "/upload" else 3
    try:
        with _urlopen_retry(req, timeout=30, attempts=attempts) as resp:
            return True, resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return False, str(e)


def device_request(url, data=None, method=None):
    """构造访问设备的 Request;配置了设备令牌则自动带 X-Device-Token。

    固件 LAN 鉴权:GET / 与 GET /status 开放,其余端点需要该头
    (开放端点会忽略它,所以统一都带)。
    """
    req = urllib.request.Request(url, data=data, method=method)
    if DEVICE_TOKEN:
        req.add_header("X-Device-Token", DEVICE_TOKEN)
    return req


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
        with _urlopen_retry(device_request(device_url("/status")), timeout=6) as resp:
            resp.read()
    except Exception:
        return False, False, "[]"

    try:
        with _urlopen_retry(device_request(device_url("/page/list")), timeout=6) as resp:
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
    req = device_request(device_url("/page/show"), data=body, method="POST")
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

    # 存历史(可选)。毫秒时间戳:避免同一秒内连续转换互相覆盖
    ts = int(time.time() * 1000)
    with open(HISTORY_DIR / f"{ts}.bin", "wb") as fp:
        fp.write(raw)
    with open(HISTORY_DIR / f"{ts}.png", "wb") as fp:
        fp.write(preview_png)
    _prune_history()

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
    data = _json_body()
    raw_b64 = data.get("raw_b64", "")
    fmt = data.get("format", "bwry2bpp")
    title = data.get("title", "")
    try:
        raw = base64.b64decode(raw_b64)
    except Exception:
        return jsonify(error="raw_b64 无效"), 400
    # 长度必须与面板像素格式一致,防脏数据写坏显存布局
    if len(raw) not in (SIZE_2BPP, SIZE_1BPP):
        return jsonify(
            error=f"raw 大小不符: {len(raw)} 字节(应为 {SIZE_2BPP} bwry2bpp "
                  f"或 {SIZE_1BPP} 1bpp)"), 400
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
        # 默认 context:校验证书与主机名(不再关闭验证)
        with urllib.request.urlopen(ctx_req, timeout=15) as resp:
            meta = json.loads(resp.read().decode("utf-8"))
        img_url = "https://cn.bing.com" + meta["images"][0]["url"]
        copyright = meta["images"][0].get("copyright", "")
        req2 = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req2, timeout=30) as resp:
            img_bytes = resp.read()
        buf = io.BytesIO(img_bytes)
        b64 = base64.b64encode(img_bytes).decode()
        return jsonify(ok=True, image_b64="data:image/jpeg;base64," + b64,
                       copyright=copyright, name="bing_daily.jpg")
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    global DEVICE_IP, UPLOAD_PORT, DEVICE_TOKEN
    if request.method == "POST":
        data = _json_body()
        # SSRF 加固:device_ip 必须是合法 IPv4 字面量(拒绝 URL/任意字符串),
        # 该值后续会被拼进 http://<ip>... 去访问设备
        if data.get("device_ip"):
            try:
                ipaddress.IPv4Address(str(data["device_ip"]).strip())
            except ValueError:
                return jsonify(ok=False, error="device_ip 需为合法 IPv4 地址"), 400
            DEVICE_IP = str(data["device_ip"]).strip()
        if data.get("device_port"):
            try:
                port = int(data["device_port"])
            except (TypeError, ValueError):
                return jsonify(ok=False, error="device_port 需为 1-65535 整数"), 400
            if not 1 <= port <= 65535:
                return jsonify(ok=False, error="device_port 需为 1-65535 整数"), 400
            UPLOAD_PORT = str(port)
        if "device_token" in data:
            # 设备令牌:8-64 位 hex(固件 NVS 里是 8 位);空串 = 清除
            tok = str(data["device_token"] or "").strip()
            if tok and not _DEVICE_TOKEN_RE.fullmatch(tok):
                return jsonify(ok=False,
                               error="device_token 需为 8-64 位十六进制字符"), 400
            DEVICE_TOKEN = tok
        return jsonify(ok=True, device_ip=DEVICE_IP, device_port=UPLOAD_PORT,
                       device_token=DEVICE_TOKEN)
    return jsonify(device_ip=DEVICE_IP, device_port=UPLOAD_PORT,
                   device_token=DEVICE_TOKEN)


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
    data = _json_body()
    page_id = data.get("page", "")
    if not page_id:
        return jsonify(ok=False, error="缺少 page"), 400
    ok, msg = device_switch_page(page_id)
    return jsonify(ok=ok, message=msg, page=page_id, device=f"{DEVICE_IP}:{UPLOAD_PORT}")


@app.route("/api/device/lifebar_birth", methods=["POST"])
def api_device_lifebar_birth():
    """配置人生进度出生日期。转发给设备存 NVS。Body: {y,m,d}"""
    data = _json_body()
    y, m, d = data.get("y"), data.get("m"), data.get("d")
    if not all(isinstance(v, int) for v in (y, m, d)):
        return jsonify(ok=False, error="y/m/d 需为整数"), 400
    url = device_url("/lifebar/birth")
    body = json.dumps({"y": y, "m": m, "d": d}).encode()
    req = device_request(url, data=body, method="POST")
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
from board import chat as chat_mod


# Short-lived data cache: the detail page fires N template previews at once
# (N = thumbnails) and each would otherwise re-hit the remote weather/news/
# stock APIs. 60s TTL keeps previews fresh enough while collapsing that
# burst to one upstream call per board.
_DATA_CACHE = {}
_DATA_TTL = 60
_DATA_CACHE_LOCK = threading.Lock()


def _get_data_cached(spec, board_id):
    now = time.time()
    with _DATA_CACHE_LOCK:
        hit = _DATA_CACHE.get(board_id)
        if hit and now - hit[0] < _DATA_TTL:
            return hit[1]
    # 慢的远端调用放锁外并发执行,只对字典读写加锁
    data = spec.get_data()
    with _DATA_CACHE_LOCK:
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
    data = _json_body()
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
    """Ask the configured AI backend (zhipu/dify), render, push.
    Body: {question: "...", auto_switch: bool} """
    data = _json_body()
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify(ok=False, error="请输入问题"), 400
    # Don't waste an e-ink refresh on a guidance screen — reject early so
    # the phone can prompt for the API config instead.
    cfg = chat_mod.get_ai_config()
    if not cfg["configured"]:
        return jsonify(ok=False, need_key=True,
                       error="未配置 AI 后端,请在「设置」页选择服务商并粘贴 Key"), 400
    auto_switch = bool(data.get("auto_switch", True))
    result = render_board("chat", template_id="text", push=True,
                          auto_switch=auto_switch,
                          data_override={"question": question})
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


@app.route("/api/ai_config", methods=["GET", "POST"])
def api_ai_config():
    """Get/set the AI backend (provider + credentials)."""
    if request.method == "GET":
        return jsonify(**chat_mod.get_ai_config())
    data = _json_body()
    try:
        chat_mod.set_ai_config(
            provider=data.get("provider"),
            zhipu_key=data.get("zhipu_key"),
            zhipu_model=data.get("zhipu_model"),
            dify_key=data.get("dify_key"),
        )
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500
    return jsonify(ok=True, **chat_mod.get_ai_config())


@app.route("/api/dify_key", methods=["GET", "POST"])
def api_dify_key():
    """Get/set the dify app API key (persisted in the /data config)."""
    from board import chat as chat_mod
    if request.method == "GET":
        key = chat_mod._api_key()
        return jsonify(configured=bool(key))
    data = _json_body()
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
    data = _json_body()
    board_id = data.get("board", "")
    spec = BOARDS.get(board_id)
    # board/template 白名单校验:存进 config 的值会被 scheduler 直接用来渲染
    if not spec:
        return jsonify(ok=False, error=f"未知 board: {board_id}"), 400
    updates = {}
    if "enabled" in data:
        if not isinstance(data["enabled"], bool):
            return jsonify(ok=False, error="enabled 需为布尔值"), 400
        updates["enabled"] = data["enabled"]
    if "smart" in data:
        if not isinstance(data["smart"], bool):
            return jsonify(ok=False, error="smart 需为布尔值"), 400
        updates["smart"] = data["smart"]
    if "template" in data:
        tpl = data["template"]
        # 空 = 用该板第一个模板;非空必须命中该板模板表
        if tpl != "" and tpl not in spec.templates:
            return jsonify(ok=False, error=f"board {board_id} 无模板 {tpl}"), 400
        updates["template"] = tpl
    if "interval_min" in data:
        try:
            interval = int(data["interval_min"])
        except (TypeError, ValueError):
            return jsonify(ok=False, error="interval_min 需为整数"), 400
        # 5 分钟下限(防刷屏风暴)~ 7 天上限
        if not 5 <= interval <= 10080:
            return jsonify(ok=False, error="interval_min 需在 5-10080 之间"), 400
        updates["interval_min"] = interval
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
