# 部署看板服务到 NAS

> 让手机通过 NAS(78:8848)访问看板功能,不再依赖开发机。

## 为什么要重新部署

NAS 上现在的 `eink-photo` 容器是**旧版**(只有图片转换 + 遥控)。
新版增加了**看板功能**(📊 tab):HTML 模板 → Chromium 截图 → 四色抖动 → 推送。
这需要安装 Playwright + Chromium + 中文字体包,所以必须**重建镜像**。

---

## 部署步骤(约 10-15 分钟,主要是下载 Chromium)

### 步骤 1:把代码传到 NAS

在开发机(我的电脑)上已经打好包:`tools/nas-service-board.tar.gz`

**方式 A:用群晖 File Station 上传**
1. 浏览器打开 NAS 的 DSM(https://192.168.100.78:5001)
2. File Station → 进入 `docker` 文件夹
3. 把 `nas-service-board.tar.gz` 上传到 `docker` 文件夹
4. 右键 → 解压

**方式 B:用 SFTP**
```bash
# 在开发机执行
sftp -P 22 wzx@192.168.100.78
# 密码输入后:
put /d/AI/youn-ink-fourcolor-firmware/tools/nas-service-board.tar.gz /docker/
# 解压(SSH 进去或 DSM 终端):
# cd /volume1/docker && tar xzf nas-service-board.tar.gz
```

### 步骤 2:SSH 进 NAS

```bash
ssh wzx@192.168.100.78
# 或如果 SSH 端口改过(之前是 30001):
ssh -p 30001 wzx@192.168.100.78
```

### 步骤 3:运行部署脚本

```bash
cd /volume1/docker/nas-service
chmod +x deploy.sh
./deploy.sh
```

脚本会自动:
1. 备份现有配置和数据
2. 停止旧容器
3. 构建新镜像(下载 Chromium,约 5-10 分钟,耐心等)
4. 启动新容器

### 步骤 4:验证

```bash
# 看板列表接口
curl http://localhost:8848/api/board/list
# 应返回: {"boards":[{"id":"almanac","label":"老黄历"}]}

# 看容器状态
docker ps | grep eink-photo
```

浏览器/手机打开 **http://192.168.100.78:8848/**,切到「📊 看板」tab。

---

## 常见问题

### Q: 构建时卡在下载 Chromium
A: 国内网络问题。脚本已设 `PLAYWRIGHT_DOWNLOAD_HOST=npmmirror`。如还卡:
```bash
export PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright
docker compose build --no-cache
```

### Q: 容器启动后内存爆了
A: Chromium 占 ~400MB。docker-compose.yml 已设 `mem_limit: 2g`。NAS 内存不够的话改小或加内存。

### Q: 设备 IP 变了(墨水屏带到公司)
A: 改 `/volume1/docker/eink-photo/docker-compose.yml` 里的 `DEVICE_IP`,然后:
```bash
cd /volume1/docker/eink-photo
docker compose up -d
```
(不用重新 build,只是重启容器读新环境变量)

### Q: 如何回滚到旧版
A: 部署脚本自动备份到 `/volume1/docker/eink-photo.backup.*`,恢复其中的 `docker-compose.yml` 后 `docker compose up -d`。

---

## 部署后的架构

```
手机浏览器                NAS (192.168.100.78:8848)          墨水屏 (192.168.100.75)
─────────                ────────────────────────           ──────────────────────
📊 看板 tab         →    Flask + Playwright + Chromium  →   /screenshot/set
  刷新预览                生成老黄历 HTML                    接收 2bpp 图片
  推送到设备              截图 400×300                       Screenshot 页全屏显示
                          四色抖动 (epaper-dithering)
                          Atkinson + 二值化
```

## 改模板后更新(日常迭代)

改了 `board/templates/almanac.html` 后,只需重启容器(不用 build):
```bash
# 把新文件传到 /volume1/docker/eink-photo/board/templates/
cd /volume1/docker/eink-photo
docker compose restart
```
秒级生效。
