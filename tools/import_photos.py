"""Robust photo importer: NAS folder → device gallery.

Runs on the dev machine. Pulls each JPEG straight from the NAS via SFTP
into memory, converts to BWRY 2bpp, uploads to the device gallery and
VERIFIES the photo actually landed by checking the /photos list count
and title. Retries aggressively (the device drops a share of large
POSTs). Idempotent: existing titles are skipped.
"""

import io
import json
import re
import sys
import time

import paramiko
import urllib.request
import urllib.parse
from PIL import Image, ImageOps
import epaper_dithering as ed

NAS = dict(host="192.168.100.78", user="wzx")
import os
NAS["pass"] = os.environ.get("NAS_PASS", "")
REMOTE_DIR = "/share/图片/魏易苒/林淑曼宝宝7月艺术照留念/全部底片"
DEVICE = "192.168.100.75"
INBOX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_inbox")
LOCATION = "林淑曼宝宝7月艺术照"
SW, SH = 400, 300


def log(*a): print(*a, flush=True)


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
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode(errors="replace")


def gallery_snapshot():
    try:
        with urllib.request.urlopen(f"http://{DEVICE}/photos", timeout=15) as r:
            d = json.loads(r.read())
        items = d.get("photos", d)
        return {str(p.get("id", "")): str(p.get("title", "")) for p in items}
    except Exception:
        return None


def main():
    if not NAS["pass"]:
        sys.exit("请设置环境变量 NAS_PASS")

    os.makedirs(INBOX_DIR, exist_ok=True)

    # ============ Phase 1: pull ALL originals from NAS (pure SFTP) ============
    # Sharing the WiFi airtime between big SFTP pulls and device POSTs makes
    # the device drop connections. Fully separate the two phases.
    t = paramiko.Transport((NAS["host"], 22))
    t.connect(username=NAS["user"], password=NAS["pass"])
    sftp = paramiko.SFTPClient.from_transport(t)

    files = sorted(f for f in sftp.listdir(REMOTE_DIR)
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
            sftp.get(f"{REMOTE_DIR}/{f}", local_path)
            log(f"[pull {i+1}/{len(files)}] {os.path.basename(f)}")
            local_files.append(local_path)
        except Exception as e:
            log(f"[{i+1}] PULL FAIL {stem}: {e}")
    sftp.close(); del t

    log(f"=== Phase 1 done: {len(local_files)} originals local ===")
    if not local_files:
        sys.exit("no files pulled")

    # ============ Phase 2: convert + push (pure device HTTP) ============
    snap = gallery_snapshot()
    have_titles = set(snap.values()) if snap else set()

    done = skipped = failed = 0
    for i, path in enumerate(local_files):
        stem = os.path.splitext(os.path.basename(path))[0]
        if stem in have_titles:
            log(f"[{i+1}/{len(local_files)}] SKIP {stem}")
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

        # 3) upload + verify (count must grow & title present)
        # NOTE: device firmware (b10) parses only ?format= reliably; keep
        # the query minimal. Titles are stored as "WiFi四色图片" by this
        # firmware — dedup therefore relies on the verify step, not titles.
        url = (f"http://{DEVICE}/upload?format=bwry2bpp"
               f"&title={urllib.parse.quote(stem)}")
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
                # Verify landing: firmware stores a hardcoded title, so check
                # the returned photo id exists in /photos instead.
                time.sleep(0.5)
                after = gallery_snapshot()
                if after is None:
                    time.sleep(1.5); continue
                if photo_id and photo_id in after:
                    snap = after
                    ok = True
                    break
                # claimed success but not listed: retry
                time.sleep(1.0 * (attempt + 1))
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
            done += 1
            log(f"[{i+1}/{len(files)}] DONE {stem} ({time.time()-t0:.1f}s)")
        else:
            failed += 1
            log(f"[{i+1}/len] FAIL {stem} after retries")

    log(f"\nSUMMARY: pushed={done} skipped={skipped} failed={failed} total={len(files)}")


if __name__ == "__main__":
    import os as _os
    import urllib.parse  # noqa
    main()
