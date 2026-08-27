#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 单板项目:目标与板型固定,直接构建本板(不再依赖多板 config.json)
BOARD_TYPE="zectrix-s3-epaper-4.2"
IDF_TARGET="esp32s3"

# 加载 .env 配置（在参数解析前，使 .env 中的值可作为默认值）
if [[ -f "$PROJECT_DIR/.env" ]]; then
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/.env"
  echo "[INFO] 已加载 .env 配置"
fi

DEFAULT_OTA_URL="${DEFAULT_OTA_URL:-https://ota.zectrix.com/xiaozhi/ota/}"

REBUILD=true
OTA_URL="$DEFAULT_OTA_URL"
FLASH_PORT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rebuild)
      REBUILD=true
      shift
      ;;
    --no-rebuild)
      REBUILD=false
      shift
      ;;
    --ota-url)
      if [[ $# -lt 2 ]]; then
        echo "[ERROR] --ota-url 需要传入地址" >&2
        exit 1
      fi
      OTA_URL="$2"
      shift 2
      ;;
    --flash)
      if [[ $# -lt 2 ]]; then
        echo "[ERROR] --flash 需要传入串口(如 COM5 或 /dev/ttyUSB0)" >&2
        exit 1
      fi
      FLASH_PORT="$2"
      shift 2
      ;;
    -h|--help)
      cat <<'EOF'
用法:
  ./build.sh [--no-rebuild] [--ota-url URL] [--flash PORT]

示例:
  ./build.sh
  ./build.sh --no-rebuild
  ./build.sh --ota-url https://ota.zectrix.com/xiaozhi/ota/
  ./build.sh --flash COM5

默认值:
  目标板   = zectrix-s3-epaper-4.2 (esp32s3，单板项目，无需指定)
  ota_url  = .env 中的 DEFAULT_OTA_URL 或 https://ota.zectrix.com/xiaozhi/ota/

参数:
  --no-rebuild   跳过 fullclean，增量编译（默认清理 build 目录后完整重编译）
  --ota-url URL  指定打包写入的 CONFIG_OTA_URL
  --flash PORT   构建完成后立即烧录到指定串口
EOF
      exit 0
      ;;
    -*)
      echo "[ERROR] 未知参数: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$OTA_URL" ]]; then
  echo "[ERROR] OTA 地址不能为空" >&2
  exit 1
fi

load_idf_env() {
  if command -v idf.py >/dev/null 2>&1; then
    return 0
  fi

  if [[ -n "${IDF_PATH:-}" && -f "${IDF_PATH}/export.sh" ]]; then
    # shellcheck disable=SC1090
    source "${IDF_PATH}/export.sh"
  fi

  if command -v idf.py >/dev/null 2>&1; then
    return 0
  fi

  for export_sh in "$HOME/esp/esp-idf/export.sh" "/root/esp/esp-idf/export.sh"; do
    if [[ -f "$export_sh" ]]; then
      # shellcheck disable=SC1090
      source "$export_sh"
      break
    fi
  done

  if ! command -v idf.py >/dev/null 2>&1; then
    echo "[ERROR] 未检测到 idf.py，请先安装 ESP-IDF 并执行 source export.sh" >&2
    exit 1
  fi
}

if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERROR] 未检测到 python3，请先安装 Python 3" >&2
  exit 1
fi

cd "$PROJECT_DIR"
load_idf_env

if [[ "$REBUILD" == "true" ]]; then
  echo "[INFO] --rebuild 已启用，清理 build 目录并删除旧发布包"
  rm -rf build
  shopt -s nullglob
  old_zips=(releases/v*_*.zip)
  if [[ ${#old_zips[@]} -gt 0 ]]; then
    rm -f "${old_zips[@]}"
  fi
  shopt -u nullglob
fi

if [[ -n "${DEFAULT_WIFI_SSID:-}" ]]; then
  echo "[INFO] 预设WiFi: $DEFAULT_WIFI_SSID"
fi

echo "[INFO] 开始打包: board=$BOARD_TYPE, target=$IDF_TARGET, ota_url=$OTA_URL"

WIFI_ARGS=()
if [[ -n "${DEFAULT_WIFI_SSID:-}" ]]; then
  WIFI_ARGS+=(--wifi-ssid "$DEFAULT_WIFI_SSID" --wifi-password "${DEFAULT_WIFI_PASSWORD:-}")
fi

python3 scripts/release.py "$BOARD_TYPE" --name "$BOARD_TYPE" \
  --ota-url "$OTA_URL" --target "$IDF_TARGET" "${WIFI_ARGS[@]}"

LATEST_ZIP="$(ls -1t releases/v*_*.zip 2>/dev/null | head -n 1 || true)"

if [[ -z "$LATEST_ZIP" ]]; then
  echo "[ERROR] 打包完成但未找到 releases/v*_*.zip" >&2
  exit 1
fi

ZIP_SIZE="$(stat -c%s "$LATEST_ZIP")"
BIN_PATH="build/merged-binary.bin"
BIN_SIZE="0"

if [[ -f "$BIN_PATH" ]]; then
  BIN_SIZE="$(stat -c%s "$BIN_PATH")"
fi

echo "[OK] 打包完成"
echo "[OK] ZIP: $LATEST_ZIP (${ZIP_SIZE} bytes)"
echo "[OK] BIN: $BIN_PATH (${BIN_SIZE} bytes)"

# ── flash 辅助：烧录合并固件 ──
if [[ -n "$FLASH_PORT" && -f "$BIN_PATH" ]]; then
  echo "[INFO] 烧录 $BIN_PATH 到 $FLASH_PORT ..."
  idf.py -p "$FLASH_PORT" flash
  echo "[OK] 烧录完成"
else
  echo "[INFO] 手动烧录: idf.py -p <PORT> flash  (或 esptool 写入 $BIN_PATH)"
fi
