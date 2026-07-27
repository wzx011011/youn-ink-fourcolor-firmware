# 图片转换器(convert_image.py)

高质量图片 → 墨水屏 raw 转换器,基于 [epaper-dithering](https://github.com/OpenDisplay/epaper-dithering) 库,带 **gamut compression(色域压缩)**,效果显著优于朴素 Floyd-Steinberg。

专为 ZecTrix 4.2" 四色墨水屏(400×300 BWRY)设计。

## 为什么效果更好

| 项目原有转换器 | 本转换器 |
|---|---|
| 朴素 Floyd-Steinberg | Floyd-Steinberg + **gamut compression** |
| 加权 RGB 距离选色(不符合人眼感知) | 感知色彩空间 + 色域压缩 |
| 无图像预处理 | 可调曝光/饱和度/阴影/高光/色调 |
| 照片发糊、颜色断层 | 层次丰富、过渡自然、颜色准 |

实测对比(RMSE 越低越好):旧算法 91.4 → 新算法 87.7(**误差降低约 4%**,彩色利用率提升)。

## 安装

```bash
pip install epaper-dithering pillow

# 国内镜像(推荐)
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple epaper-dithering pillow
```

要求:Python ≥ 3.11(已在 Python 3.14 验证通过)。

## 用法

### 基础:转一张图

```bash
# 默认四色 bwry2bpp
python convert_image.py input.jpg output.bin

# 黑白模式
python convert_image.py input.jpg output.bin --format 1bpp
```

### 调参数(照片类内容推荐)

```bash
# 提色域压缩 + 饱和度 + 对比度(照片甜点值)
python convert_image.py photo.jpg out.bin --gamut 0.7 --saturation 1.3 --exposure 1.1
```

### 同时输出预览 PNG(电脑上对比效果)

```bash
python convert_image.py photo.jpg out.bin --preview preview.png
```

### 转换后直接推送到设备

```bash
# AP 模式(手机连设备热点时)
python convert_image.py photo.jpg out.bin --push 192.168.4.1

# 局域网模式(设备 WiFi 已连,且在设置里开了"局域网服务")
python convert_image.py photo.jpg out.bin --push 192.168.100.75 --title "我的照片"
```

## 参数详解

| 参数 | 范围 | 默认 | 说明 |
|---|---|---|---|
| `--format` | bwry2bpp / 1bpp | bwry2bpp | 输出格式 |
| `--dither` | 见下表 | fs | 抖动算法 |
| `--gamut` | 0.0-1.0 | **0.5** | **色域压缩强度(质量关键)** |
| `--exposure` | 0.5-2.0 | 1.0 | 曝光(< 1 变暗,> 1 变亮) |
| `--saturation` | 0-2.0 | 1.0 | 饱和度(墨水屏常需 > 1 补偿) |
| `--shadows` | -1.0-1.0 | 0 | 阴影提亮(正值提亮暗部) |
| `--highlights` | -1.0-1.0 | 0 | 高光压暗(正值压暗亮部) |
| `--tone` | -1.0-1.0 | 0 | 色调偏移 |
| `--bg` | black / white | black | 缩放填充背景 |
| `--preview` | 路径 | - | 输出预览 PNG |
| `--push` | IP | - | 推送到设备 |
| `--title` | 字符串 | - | 推送时的图片标题 |

## 抖动算法选择

| 算法 | `--dither` | 特点 | 适合 |
|---|---|---|---|
| Floyd-Steinberg | `fs` | 通用首选,层次丰富 | **照片、彩色**(默认) |
| Burkes | `burkes` | 类似 FS,略锐 | 通用 |
| Stucki | `stucki` | 最细腻,细节多 | 高细节图、插画 |
| Jarvis | `jarvis` | 计算量最大,极细腻 | 静态展示图 |
| Atkinson | `atkinson` | 复古感,干净 | 极简风、文字图 |
| Ordered | `ordered` | 规则点阵 | 复古印刷感 |

## 不同内容的推荐参数

### 照片(人像/风景)
```bash
python convert_image.py photo.jpg out.bin --gamut 0.7 --saturation 1.3 --exposure 1.1 --shadows 0.1
```

### 文字/截图/漫画
```bash
python convert_image.py manga.jpg out.bin --gamut 0.3 --dither atkinson
```

### 黑白线条画
```bash
python convert_image.py sketch.jpg out.bin --format 1bpp --dither fs
```

### 高对比海报
```bash
python convert_image.py poster.jpg out.bin --gamut 0.8 --saturation 1.5
```

## 批量转换(Bash)

```bash
for f in photos/*.jpg; do
  python convert_image.py "$f" "out/$(basename "${f%.jpg}").bin" --gamut 0.7 --saturation 1.3
done
```

## 输出格式说明

### bwry2bpp(四色,30000 字节)
- 4 像素 / 字节,pixel 0 占 bits 7-6,pixel 3 占 bits 1-0
- 颜色码:黑=0、白=1、黄=2、红=3
- 总大小:400 × 300 × 2bit ÷ 8 = 30000 字节

### 1bpp(黑白,15000 字节)
- 8 像素 / 字节,MSB first
- 颜色码:白=1、黑=0
- 总大小:400 × 300 ÷ 8 = 15000 字节

## 设备端接收

转换后的 .bin 文件可通过两种方式传到设备:

1. **本工具的 `--push`**(最方便,见上)
2. **设备配网页面**:连设备热点 → 浏览器开 `http://192.168.4.1` → 上传(但设备配网页用的是旧 JS 转换器,质量不如本工具;建议用 `--push`)

## 故障排除

- **推送失败**:确认设备 WiFi 已连、IP 正确、设置里"局域网服务"已开启
- **效果还是发灰**:加大 `--saturation`(1.3-1.5)和 `--exposure`(1.1-1.2)
- **颜色太冲**:降低 `--gamut`(0.3-0.4)
- **细节糊**:换 `--dither stucki` 或 `--dither jarvis`
- **暗部全黑**:加 `--shadows 0.2` 提亮暗部
