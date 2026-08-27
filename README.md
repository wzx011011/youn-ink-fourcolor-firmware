# Youn Ink Four Color

面向 ESP32-S3 墨水屏设备的四色电子墨水屏系统。整条链路是：**NAS 上的 Flask 服务做图片转换与看板渲染 → 通过局域网 HTTP 推送给 ESP32-S3 设备 → 设备以 2bpp BWRY 四色刷屏显示**；手机浏览器访问 NAS 页面即可传图、遥控设备页面、配置定时推屏和 AI 对话。

## 2BP 四色图像链路

![Youn Ink Four Color 2BP BWRY architecture](README-2bp-architecture.png)

任意来源的图片（手机上传、Bing 壁纸、NAS 照片目录）在 NAS 侧经 `epaper-dithering`（gamut compression 色域压缩 + 抖动）转换为 `2BP BWRY`（黑、白、红、黄，400×300，30000 字节）后，通过 Wi-Fi POST 到设备的 `/upload` 接口。本仓库的 2BP 四色链路与 NOTE4 的 4BP 黑白灰阶相册独立维护：面板颜色、像素格式和刷新驱动均不同。

## 目录结构

```text
.
├── firmware/                    ESP32-IDF 固件（单板：zectrix-s3-epaper-4.2）
│   ├── main/                    应用源码：RawDraw UI、页面渲染、屏幕驱动、AP 传图
│   ├── components/              组件（屏幕驱动、字体等）
│   ├── scripts/release.py       打包脚本（merge-bin + zip）
│   ├── build.sh                 Linux/CI 构建脚本（单板直接构建）
│   ├── idfbuild.ps1             Windows 本机构建包装
│   └── tools/layout_adjuster.html  RawDraw 布局微调工具（浏览器打开即用）
├── tools/
│   ├── nas-service/             NAS 侧 Flask 服务（Docker 部署，见下）
│   │   ├── app.py               HTTP 服务：图片转换/推送/遥控/定时/AI 对话
│   │   ├── board/               看板：天气、新闻、老黄历、股市、AI 对话（纯 PIL 渲染）
│   │   ├── templates/           手机端页面（index.html / detail.html）
│   │   ├── Dockerfile           python:3.12 + Noto CJK + gunicorn(gthread)
│   │   ├── docker-compose.yml   端口 8848→8096，/data 卷持久化
│   │   └── update-nas.py        代码热同步到 NAS 并重启容器
│   ├── convert_image.py         命令行图片 → 墨水屏 raw 转换器
│   └── import_photos.py         NAS 照片目录批量导入设备相册
└── docs/                        设计文档和实现记录
```

## 固件

固件位于 `firmware/`，基于 ESP-IDF，目标板固定为 **zectrix-s3-epaper-4.2**（ZecTrix ESP32-S3 4.2 寸四色 BWRY 屏，400×300），同时保留 1bpp 黑白屏兼容（RawDraw 主题层会把红/黄语义色降级为黑白样式）。

### 构建

Windows 本机（使用本地安装的 ESP-IDF v6.0）：

```powershell
cd firmware
.\idfbuild.ps1 build          # 任意 idf.py 子命令都会透传
```

Linux / CI：

```bash
cd firmware
./build.sh                    # 完整重编译 + 打包 releases/v*_*.zip
./build.sh --no-rebuild       # 增量编译
./build.sh --flash COM5       # 构建后烧录（Windows 串口示例）
```

已配置好 ESP-IDF 环境时也可以直接：

```bash
cd firmware
idf.py set-target esp32s3
idf.py build
```

## nas-service 部署（NAS Docker）

服务以单容器跑在 NAS 上（gunicorn gthread，1 worker × 8 线程），容器内监听 **8096**，宿主映射为 **8848**。NAS 作为 HTTP 客户端访问局域网里的墨水屏设备（默认 `DEVICE_IP=192.168.100.75:80`，可在网页「设置」里改）。

```bash
cd tools/nas-service
docker compose up -d --build
# 访问 http://<NAS-IP>:8848/
```

### 可选鉴权 NAS_SERVICE_TOKEN

在 `docker-compose.yml` 的 environment 里设置 `NAS_SERVICE_TOKEN` 后，所有 `/api/*` 请求（含 GET，防止 AI Key 等配置被读取）必须携带 `X-Auth-Token: <同样的值>`，否则返回 401；前端会弹窗让你输入一次并存入 localStorage。不设置则服务照常运行（启动日志会给出无鉴权警告）。公网可达时强烈建议开启。

### 设备令牌（固件 LAN 鉴权）

固件侧 LAN 模式启用设备 token 鉴权后，设备要求 NAS 的推送/控制请求携带 `X-Device-Token` 头。令牌在设备「设置」页查看，填入 NAS 网页「设置 → 设备连接 → 设备令牌」即可（也可用环境变量 `DEVICE_TOKEN` 固化），NAS 对设备的所有请求会自动带上该头。

## 设备 HTTP API 一览

NAS（及调试用的 curl）直接访问设备：

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/status` | GET | 设备状态与可达性探测（NAS「遥控」页用它判断在线） |
| `/page/list` | GET | 当前页面列表及激活页（遥控按钮数据源） |
| `/page/show` | POST | 切换设备页面，body `{"page":"weather"}` |
| `/upload` | POST | 推送图片进相册，query `format=bwry2bpp&title=...`，body 为 raw 字节 |
| `/screenshot/set` | POST | 推送看板截图到常驻「看板」页，query `format=bwry2bpp&label=...` |
| `/lifebar/birth` | POST | 配置「人生进度」出生日期，body `{"y":1990,"m":1,"d":1}`，写入 NVS |

示例：

```bash
curl http://192.168.100.75/status
curl -X POST "http://192.168.100.75/upload?format=bwry2bpp&title=hello" \
     --data-binary @out.bin
```

## 命令行工具

```bash
# 单张图转换（可 --push 直推设备）
python tools/convert_image.py photo.jpg out.bin --gamut 0.6 --preview preview.png

# NAS 照片目录批量导入设备相册（幂等，已传清单存 tools/uploaded_ids.json）
export NAS_HOST=... NAS_USER=... NAS_PASS=... NAS_REMOTE_DIR=/share/photos
python tools/import_photos.py --device 192.168.100.75
```

## Git 提交范围

建议提交：`firmware/`（除 build 产物与 sdkconfig）、`tools/`、`docs/`、根目录 README 与配置。

不要提交：`firmware/build*/`、`firmware/managed_components/`、`firmware/sdkconfig`、`firmware/releases/`、NAS 密码/令牌、`tools/_inbox/`、`tools/uploaded_ids.json` 等本地数据文件。
