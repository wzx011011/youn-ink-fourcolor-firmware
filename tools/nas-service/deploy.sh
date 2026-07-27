#!/bin/bash
# ====================================================================
# 墨水屏看板服务 - NAS 部署脚本
# 在 NAS 上执行(通过 SSH 或 DSM 终端)
# 作用:更新 eink-photo 容器到带「看板」功能的新版(Playwright + Chromium)
# ====================================================================
set -e

DEPLOY_DIR="/volume1/docker/eink-photo"
BACKUP_DIR="/volume1/docker/eink-photo.backup.$(date +%Y%m%d_%H%M%S)"

echo "========================================"
echo "  墨水屏看板服务部署"
echo "========================================"

# 1. 检查当前目录
if [ ! -f "./docker-compose.yml" ] && [ ! -f "./nas-service/docker-compose.yml" ]; then
    echo "❌ 请在解压后的 nas-service 目录(或其父目录)运行此脚本"
    echo "   应该能看到 docker-compose.yml 文件"
    exit 1
fi

# 如果在 nas-service 父目录,进到子目录
if [ -f "./nas-service/docker-compose.yml" ] && [ ! -f "./docker-compose.yml" ]; then
    cd nas-service
fi

echo ""
echo "📦 1/5 备份现有部署..."
if [ -d "$DEPLOY_DIR" ]; then
    echo "   旧版本备份到: $BACKUP_DIR"
    # 只备份配置和历史数据,不备份旧镜像
    mkdir -p "$BACKUP_DIR"
    [ -f "$DEPLOY_DIR/docker-compose.yml" ] && cp "$DEPLOY_DIR/docker-compose.yml" "$BACKUP_DIR/"
    [ -d "$DEPLOY_DIR/data" ] && cp -r "$DEPLOY_DIR/data" "$BACKUP_DIR/"
else
    echo "   首次部署,无需备份"
fi

echo ""
echo "📂 2/5 准备部署目录..."
mkdir -p "$DEPLOY_DIR/data"
# 把当前目录的所有文件复制到部署目录
cp -r ./* "$DEPLOY_DIR/" 2>/dev/null || true
cp -r ./board "$DEPLOY_DIR/" 2>/dev/null || true
cp -r ./templates "$DEPLOY_DIR/" 2>/dev/null || true

cd "$DEPLOY_DIR"
echo "   部署目录: $DEPLOY_DIR"

echo ""
echo "⏬ 3/5 停止旧容器..."
docker compose down 2>/dev/null || docker-compose down 2>/dev/null || true

echo ""
echo "🔨 4/5 构建新镜像(下载 Chromium ~500MB,首次约 5-10 分钟)..."
echo "   如果卡在下载,检查 NAS 网络或重试"
docker compose build --no-cache 2>&1 | tail -20

echo ""
echo "🚀 5/5 启动新容器..."
docker compose up -d 2>&1 | tail -5

echo ""
echo "========================================"
echo "  ✅ 部署完成!"
echo "========================================"
echo ""
echo "访问地址: http://$(hostname -I | awk '{print $1}'):8848/"
echo "         (或用 NAS 局域网 IP:8848)"
echo ""
echo "验证:"
echo "  curl http://localhost:8848/api/board/list"
echo "  应返回: {\"boards\":[{\"id\":\"almanac\",\"label\":\"老黄历\"}]}"
echo ""
echo "看「看板」tab:浏览器打开上面地址,切到「📊 看板」"
echo ""
echo "如需回滚:恢复 $BACKUP_DIR 的 docker-compose.yml 并 docker compose up -d"
