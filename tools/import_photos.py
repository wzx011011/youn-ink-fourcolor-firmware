"""Robust photo importer: NAS folder → device gallery.

Runs on the dev machine. Pulls each JPEG straight from the NAS via SFTP
into a local inbox, converts to BWRY 2bpp, uploads to the device gallery
and VERIFIES the photo actually landed by checking the returned photo id
against /photos. Retries aggressively (the device drops a share of large
POSTs). Idempotent: uploaded files are recorded in tools/uploaded_ids.json
(stem → device photo id) and skipped on rerun — the firmware stores a
fixed title, so title-based dedup doesn't work.

All connection details come from CLI args / env vars (no personal paths
or credentials in code):

  export NAS_PASS='...' NAS_HOST=... NAS_USER=... NAS_REMOTE_DIR=/share/...
  python import_photos.py [--device 192.168.4.1]
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import paramiko
from PIL import Image, ImageOps
import epaper_dithering as ed

SW, SH = 400, 300
INBOX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_inbox")
DEVICE_TOKEN = ""  # set from --device-token / env in main()
# 已上传清单:{"<文件名 stem>": "<设备返回的 photo id>"}
# 固件忽略上传时的 title(统一存固定标题),按标题去重失效 → 本地持久化
MANIFEST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "uploaded_ids.json")


def log(*a): print(*a, flush=True)


def load_manifest():
    try:
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_manifest(manifest):
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def to_2bpp(img):
    d = ed.dither_image(img, ed.ColorScheme.BWRY,
                        mode=ed.DitherMode.FLOYD_STEINBERG,
                        serpentine=True, saturation=1.25,
                        exposure=1.08, gamut=0.7)
    px = d.load()
    out = bytearray(SW * SH * 2 // 8)
    for y in range(SH):
        for x in range(SW):
            c = px[x, y] & 0x03
            p = y * SW + x
            out[p >> 2] |= c << (6 - ((p & 3) * 2))
    return bytes(out)


def fit(img):
    img = ImageOps.exif_transpose(img).convert("RGB")
    scale = min(SW / img.width, SH / img.height)
    nw, nh = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    img = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", (SW, SH), (255, 255, 255))
    canvas.paste(img, ((SW - nw) // 2, (SH - nh) // 2))
    return canvas


def http_post(url, data, timeout=30):
    headers = {"Content-Type": "application/octet-stream"}
    if DEVICE_TOKEN:
        headers["X-Device-Token"] = DEVICE_TOKEN
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode(errors="replace")


def gallery_snapshot(device):
    try:
        req = urllib.request.Request(f"http://{device}/photos")
        if DEVICE_TOKEN:
            req.add_header("X-Device-Token", DEVICE_TOKEN)
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read())
        items = d.get("photos", d)
        return {str(p.get("id", "")): str(p.get("title", "")) for p in items}
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="NAS 照片目录 → 墨水屏相册 批量导入")
    ap.add_argument("--nas-host", default=os.environ.get("NAS_HOST", ""),
                    help="NAS 地址(或环境变量 NAS_HOST),必填")
    ap.add_argument("--nas-user", default=os.environ.get("NAS_USER", ""),
                    help="NAS 用户名(或环境变量 NAS_USER),必填")
    ap.add_argument("--remote-dir", default=os.environ.get("NAS_REMOTE_DIR", ""),
                    help="NAS 上待导入的照片目录(或环境变量 NAS_REMOTE_DIR),必填")
    ap.add_argument("--device", default=os.environ.get("DEVICE_IP", "192.168.4.1"),
                    help="墨水屏设备 IP(默认 AP 模式 192.168.4.1)")
    ap.add_argument("--device-token", default=os.environ.get("DEVICE_TOKEN", ""),
                    help="设备 LAN 鉴权令牌(设备「设置」页可见;AP 模式可省)")
    args = ap.parse_args()

    global DEVICE_TOKEN
    DEVICE_TOKEN = args.device_token

    missing = [name for name, val in
               (("NAS_HOST/--nas-host", args.nas_host),
                ("NAS_USER/--nas-user", args.nas_user),
                ("NAS_REMOTE_DIR/--remote-dir", args.remote_dir)) if not val]
    if missing:
        sys.exit(f"[ERROR] 缺少必填参数: {', '.join(missing)}\n"
                 "  可用命令行参数或同名环境变量提供;密码用 NAS_PASS 环境变量。")
    if not os.environ.get("NAS_PASS"):
        sys.exit("[ERROR] 未设置 NAS_PASS 环境变量:export NAS_PASS='...' 后重试")

    os.makedirs(INBOX_DIR, exist_ok=True)

    # ============ Phase 1: pull ALL originals from NAS (pure SFTP) ============
    # Sharing the WiFi airtime between big SFTP pulls and device POSTs makes
    # the device drop connections. Fully separate the two phases.
    t = paramiko.Transport((args.nas_host, 22))
    t.connect(username=args.nas_user, password=os.environ["NAS_PASS"])
    sftp = paramiko.SFTPClient.from_transport(t)

    files = sorted(f for f in sftp.listdir(args.remote_dir)
                   if f.lower().endswith((".jpg", ".jpeg")))
    log(f"remote jpgs: {len(files)}")

    local_files = []
    for i, f in enumerate(files):
        stem = os.path.splitext(os.path.basename(f))[0]
        local_path = os.path.join(INBOX_DIR, f)
        if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            local_files.append(local_path)
            continue
        try:
            sftp.get(f"{args.remote_dir}/{f}", local_path)
            log(f"[pull {i+1}/{len(files)}] {os.path.basename(f)}")
            local_files.append(local_path)
        except Exception as e:
            log(f"[{i+1}] PULL FAIL {stem}: {e}")
    sftp.close(); del t

    log(f"=== Phase 1 done: {len(local_files)} originals local ===")
    if not local_files:
        sys.exit("no files pulled")

    # ============ Phase 2: convert + push (pure device HTTP) ============
    manifest = load_manifest()
    log(f"manifest: {len(manifest)} already uploaded ({MANIFEST_PATH})")

    done = skipped = failed = 0
    for i, path in enumerate(local_files):
        stem = os.path.splitext(os.path.basename(path))[0]
        if stem in manifest:
            log(f"[{i+1}/{len(local_files)}] SKIP {stem} (已在上传清单)")
            skipped += 1
            continue

        # 2) convert (from the local copy pulled in phase 1)
        t0 = time.time()
        try:
            with Image.open(path) as im:
                img = fit(im)
            raw = to_2bpp(img)
        except Exception as e:
            log(f"[{i+1}] CONVERT FAIL {stem}: {e}")
            failed += 1
            continue

        # 3) upload + verify (returned photo id must appear in /photos)
        # NOTE: device firmware (b10) parses only ?format= reliably; keep
        # the query minimal. Titles are stored as a fixed string by this
        # firmware — dedup therefore relies on the local manifest, not titles.
        url = (f"http://{args.device}/upload?format=bwry2bpp"
               f"&title={urllib.parse.quote(stem)}")
        photo_id = ""
        ok = False
        consecutive_rst = 0
        cooldowns = 0
        for attempt in range(8):
            try:
                st, body = http_post(url, raw)
                consecutive_rst = 0
                if '"success":true' not in body:
                    # Device refused (e.g. storage full) — retrying won't help
                    log(f"[{i+1}]   device refused: {body[:60]}")
                    break
                m = re.search(r'"id":"([^"]+)"', body)
                photo_id = m.group(1) if m else ""
                if not photo_id:
                    # 旧固件不回 id:设备已确认成功,无法进一步验证,接受
                    ok = True
                    break
                # Verify landing: check the returned photo id exists in /photos
                time.sleep(0.5)
                after = gallery_snapshot(args.device)
                if after is None:
                    time.sleep(1.5); continue
                if photo_id in after:
                    ok = True
                    break
                # claimed success but not listed: retry
                time.sleep(1.0 * (attempt + 1))
            except urllib.error.HTTPError as e:
                # 4xx/5xx 是设备明确拒绝,重试没有意义,直接失败
                err_body = ""
                try:
                    err_body = e.read().decode(errors="replace")[:80]
                except Exception:
                    pass
                log(f"[{i+1}]   HTTP {e.code},不重试: {err_body}")
                break
            except Exception as e:
                consecutive_rst += 1
                # ~8 rapid uploads exhaust the device's TCP TIME_WAIT slots;
                # a run of resets means the pool is drained — cool down
                # instead of burning attempts.
                if consecutive_rst >= 2 and cooldowns < 4:
                    cooldowns += 1
                    log(f"[{i+1}]   连续RST,冷却75s ({cooldowns}/4)")
                    time.sleep(75)
                    consecutive_rst = 0
                else:
                    time.sleep(2.0 * (attempt + 1))

        if ok:
            # 持久化到清单(即使没有 photo id 也记录,保证重跑跳过)
            manifest[stem] = photo_id
            save_manifest(manifest)
            done += 1
            log(f"[{i+1}/{len(files)}] DONE {stem} id={photo_id or '-'} "
                f"({time.time()-t0:.1f}s)")
        else:
            failed += 1
            log(f"[{i+1}/len] FAIL {stem} after retries")

    log(f"\nSUMMARY: pushed={done} skipped={skipped} failed={failed} "
        f"total={len(files)} manifest={len(manifest)}")


if __name__ == "__main__":
    main()
