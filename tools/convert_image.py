#!/usr/bin/env python3
"""
 convert_image.py — 高质量图片 → 墨水屏 raw 转换器(方案 A)

 基于 epaper-dithering 库(gamut compression 色域压缩),效果远好于朴素
 Floyd-Steinberg。专为 ZecTrix 4.2" 四色墨水屏(400x300 BWRY)设计。

 用法:
   # 基础:转一张图(默认四色 bwry2bpp)
   python convert_image.py input.jpg output.bin

   # 指定黑白模式
   python convert_image.py input.jpg output.bin --format 1bpp

   # 调参数(照片类内容推荐加色域压缩 + 提对比度)
   python convert_image.py photo.jpg out.bin --gamut 0.6 --exposure 1.1 --saturation 1.2

   # 同时输出预览 PNG(方便在电脑上对比效果)
   python convert_image.py photo.jpg out.bin --preview preview.png

   # 自动推送到设备(AP 模式 192.168.4.1 或局域网 IP)
   python convert_image.py photo.jpg out.bin --push 192.168.100.75

 输出格式:
   - bwry2bpp: 30000 字节,4 像素/字节,黑=0/白=1/黄=2/红=3
   - 1bpp:     15000 字节,8 像素/字节,白=1/黑=0

 依赖:
   pip install epaper-dithering pillow
   (国内镜像: pip install -i https://pypi.tuna.tsinghua.edu.cn/simple epaper-dithering pillow)
"""

import argparse
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image
import epaper_dithering as ed

# ============================================================
# 设备参数(ZecTrix 4.2" 四色墨水屏)
# ============================================================
SCREEN_WIDTH = 400
SCREEN_HEIGHT = 300
SIZE_2BPP = SCREEN_WIDTH * SCREEN_HEIGHT * 2 // 8   # 30000 bytes
SIZE_1BPP = SCREEN_WIDTH * SCREEN_HEIGHT // 8        # 15000 bytes


# ============================================================
# 图片预处理:缩放到屏幕尺寸(保持比例,黑边填充)
# ============================================================
def fit_to_screen(img: Image.Image, bg=(0, 0, 0)) -> Image.Image:
    """等比缩放到 400x300,不足部分用 bg 填充(默认黑底,避免白边过亮)。"""
    img = img.convert("RGB")
    src_w, src_h = img.size
    scale = min(SCREEN_WIDTH / src_w, SCREEN_HEIGHT / src_h)
    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    resized = img.resize((new_w, new_h), Image.LANCZOS)

    canvas = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), bg)
    offset_x = (SCREEN_WIDTH - new_w) // 2
    offset_y = (SCREEN_HEIGHT - new_h) // 2
    canvas.paste(resized, (offset_x, offset_y))
    return canvas


# ============================================================
# 核心:抖动 + 编码
# ============================================================
def convert_bwry2bpp(
    img: Image.Image,
    mode=ed.DitherMode.FLOYD_STEINBERG,
    exposure=1.0,
    saturation=1.0,
    shadows=0.0,
    highlights=0.0,
    tone=0.0,
    gamut=0.5,
):
    """
    转四色 BWRY 2bpp raw。
    gamut(色域压缩)是质量关键:把 4 色色域外的颜色先压缩进来,再抖动。
      - 0.0  关闭(等同朴素 FS,效果一般)
      - 0.5  推荐(照片类内容的甜点值)
      - 1.0  最大压缩(色彩更准但层次略减)
    返回 (raw_bytes 30000 字节, PIL P模式预览图)
    """
    dithered = ed.dither_image(
        img,
        ed.ColorScheme.BWRY,
        mode=mode,
        serpentine=True,          # 蛇形扫描,减少方向性噪点
        exposure=exposure,
        saturation=saturation,
        shadows=shadows,
        highlights=highlights,
        tone=tone,
        gamut=gamut,
    )
    # dithered 是 P 模式,调色板像素值正好:0=黑 1=白 2=黄 3=红
    # 设备 2bpp 编码:黑=0 白=1 黄=2 红=3,完全一致,直接打包
    indexed = dithered.load()
    out = bytearray(SIZE_2BPP)
    for y in range(SCREEN_HEIGHT):
        for x in range(SCREEN_WIDTH):
            color = indexed[x, y] & 0x03   # 只取低 2 位(P 模式可能返回 int)
            p = y * SCREEN_WIDTH + x
            out[p >> 2] |= color << (6 - ((p & 3) * 2))
    return bytes(out), dithered


def convert_1bpp(
    img: Image.Image,
    mode=ed.DitherMode.FLOYD_STEINBERG,
    exposure=1.0,
    saturation=1.0,
    shadows=0.0,
    highlights=0.0,
    tone=0.0,
):
    """转黑白 1bpp raw。"""
    dithered = ed.dither_image(
        img,
        ed.ColorScheme.MONO,
        mode=mode,
        serpentine=True,
        exposure=exposure,
        saturation=saturation,
        shadows=shadows,
        highlights=highlights,
        tone=tone,
    )
    # MONO 输出 P 模式:0=黑 1=白
    indexed = dithered.load()
    out = bytearray(SIZE_1BPP)
    for y in range(SCREEN_HEIGHT):
        for x in range(SCREEN_WIDTH):
            white = 1 if (indexed[x, y] & 0x01) else 0
            p = y * SCREEN_WIDTH + x
            if white:
                out[p >> 3] |= 1 << (7 - (p & 7))
    return bytes(out), dithered


# ============================================================
# 推送到设备
# ============================================================
def push_to_device(raw: bytes, host: str, fmt: str = "bwry2bpp", title: str = "", token: str = ""):
    """通过设备的 /upload API 推送 raw 数据。token: 设备 LAN 鉴权令牌(设备设置页可见)。"""
    url = f"http://{host}/upload?format={urllib.parse.quote(fmt)}"
    if title:
        url += "&title=" + urllib.parse.quote(title)  # 标题拼进 URL(相册里可见)
    if token:
        url += "&token=" + urllib.parse.quote(token)  # LAN 模式鉴权(AP 模式可省)
    print(f"推送到 {url.split(chr(63))[0]} ...")
    req = urllib.request.Request(url, data=raw, method="POST")
    req.add_header("Content-Type", "application/octet-stream")
    if token:
        req.add_header("X-Device-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            print(f"设备响应 ({resp.status}): {body[:200]}")
            return True
    except Exception as e:
        print(f"推送失败: {e}", file=sys.stderr)
        return False


# ============================================================
# CLI
# ============================================================
DITHER_MODES = {
    "fs": ed.DitherMode.FLOYD_STEINBERG,    # Floyd-Steinberg,通用首选
    "burkes": ed.DitherMode.BURKES,         # 库默认,类似 FS 略锐
    "atkinson": ed.DitherMode.ATKINSON,     # 复古感,层次少但干净
    "stucki": ed.DitherMode.STUCKI,         # 高质量,细节丰富
    "jarvis": ed.DitherMode.JARVIS_JUDICE_NINKE,  # 最细腻,计算量最大
    "sierra": ed.DitherMode.SIERRA,
    "ordered": ed.DitherMode.ORDERED,       # 有序抖动,规则点阵
}


def main():
    ap = argparse.ArgumentParser(
        description="高质量图片 → 墨水屏 raw 转换器(基于 epaper-dithering)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  python convert_image.py input.jpg output.bin\n"
               "  python convert_image.py photo.jpg out.bin --gamut 0.6 --saturation 1.2\n"
               "  python convert_image.py pic.jpg out.bin --preview preview.png --push 192.168.4.1\n",
    )
    ap.add_argument("input", help="输入图片(jpg/png/webp/bmp 等)")
    ap.add_argument("output", help="输出 .bin 文件路径")
    ap.add_argument("--format", choices=["bwry2bpp", "1bpp"], default="bwry2bpp",
                    help="输出格式(默认 bwry2bpp 四色)")
    ap.add_argument("--dither", choices=list(DITHER_MODES.keys()), default="fs",
                    help="抖动算法(默认 fs=Floyd-Steinberg)")
    ap.add_argument("--gamut", type=float, default=0.5,
                    help="色域压缩 0.0-1.0(默认 0.5,照片推荐 0.5-0.7)")
    ap.add_argument("--exposure", type=float, default=1.0, help="曝光倍数(默认 1.0)")
    ap.add_argument("--saturation", type=float, default=1.0, help="饱和度倍数(默认 1.0)")
    ap.add_argument("--shadows", type=float, default=0.0, help="阴影提亮 -1.0-1.0(默认 0)")
    ap.add_argument("--highlights", type=float, default=0.0, help="高光压暗 -1.0-1.0(默认 0)")
    ap.add_argument("--tone", type=float, default=0.0, help="色调偏移 -1.0-1.0(默认 0)")
    ap.add_argument("--bg", default="black", choices=["black", "white"],
                    help="缩放填充背景(默认 black)")
    ap.add_argument("--preview", help="同时输出预览 PNG(方便电脑上对比效果)")
    ap.add_argument("--push", help="转换后自动推送到设备(填 IP,如 192.168.4.1)")
    ap.add_argument("--title", default="", help="推送时的图片标题(配合 --push)")
    ap.add_argument("--device-token", default=os.environ.get("DEVICE_TOKEN", ""),
                    help="设备 LAN 鉴权令牌(设备「设置」页可见;AP 模式可省;或环境变量 DEVICE_TOKEN)")
    args = ap.parse_args()

    mode = DITHER_MODES[args.dither]
    bg = (0, 0, 0) if args.bg == "black" else (255, 255, 255)

    # 1. 加载 + 缩放
    print(f"加载 {args.input} ...")
    img = Image.open(args.input)
    print(f"  原始尺寸: {img.size}")
    fitted = fit_to_screen(img, bg=bg)
    print(f"  缩放到: {fitted.size}")

    # 2. 抖动转换
    fmt = args.format
    print(f"转换 [{fmt}] dither={args.dither} gamut={args.gamut} "
          f"exposure={args.exposure} saturation={args.saturation} ...")
    common = dict(mode=mode, exposure=args.exposure, saturation=args.saturation,
                  shadows=args.shadows, highlights=args.highlights, tone=args.tone)
    if fmt == "bwry2bpp":
        raw, preview = convert_bwry2bpp(fitted, gamut=args.gamut, **common)
        expected = SIZE_2BPP
    else:
        raw, preview = convert_1bpp(fitted, **common)
        expected = SIZE_1BPP

    assert len(raw) == expected, f"输出大小异常: {len(raw)} != {expected}"
    print(f"  输出 {len(raw)} 字节 ✓")

    # 3. 写文件
    Path(args.output).write_bytes(raw)
    print(f"已写入 {args.output}")

    # 4. 预览 PNG
    if args.preview:
        # 把 P 模式预览放大保存,方便看清
        preview_rgb = preview.convert("RGB").resize(
            (SCREEN_WIDTH * 2, SCREEN_HEIGHT * 2), Image.NEAREST
        )
        preview_rgb.save(args.preview)
        print(f"预览图已写入 {args.preview}")

    # 5. 推送
    if args.push:
        push_to_device(raw, args.push, fmt, args.title, args.device_token)


if __name__ == "__main__":
    main()
