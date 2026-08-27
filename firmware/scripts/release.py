import os
import re
import shlex
import sys
import json
import zipfile
import argparse
from pathlib import Path
from typing import Optional

# Switch to project root directory
os.chdir(Path(__file__).resolve().parent.parent)

# 命令行/脚本拼接进 os.system 的参数全部走白名单校验,防止注入
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._\-]+$")
_SAFE_BOARD = re.compile(r"^[a-z0-9.\-]+$")
_SAFE_TARGET = re.compile(r"^[a-z0-9]+$")
_SAFE_URL = re.compile(r"^[A-Za-z0-9+.\-:/_%?#=&]+$")

################################################################################
# Common utility functions
################################################################################

def _validate(label: str, value: str, pattern) -> str:
    if not pattern.fullmatch(value):
        print(f"[ERROR] 参数 {label} 含非法字符: {value!r}", file=sys.stderr)
        sys.exit(1)
    return value


def get_board_type_from_compile_commands() -> Optional[str]:
    """Parse the current compiled BOARD_TYPE from build/compile_commands.json"""
    compile_file = Path("build/compile_commands.json")
    if not compile_file.exists():
        return None
    with compile_file.open() as f:
        data = json.load(f)
    for item in data:
        if not item["file"].endswith("main.cc"):
            continue
        cmd = item["command"]
        if "-DBOARD_TYPE=\\\"" in cmd:
            return cmd.split("-DBOARD_TYPE=\\\"")[1].split("\\\"")[0].strip()
    return None


def get_project_version() -> Optional[str]:
    """Read set(PROJECT_VER "x.y.z") from root CMakeLists.txt"""
    with Path("CMakeLists.txt").open() as f:
        for line in f:
            if line.startswith("set(PROJECT_VER"):
                return line.split("\"")[1]
    return None


def merge_bin() -> None:
    if os.system("idf.py merge-bin") != 0:
        print("merge-bin failed", file=sys.stderr)
        sys.exit(1)


def zip_bin(name: str, version: str) -> None:
    """Zip build/merged-binary.bin to releases/v{version}_{name}.zip"""
    out_dir = Path("releases")
    out_dir.mkdir(exist_ok=True)
    output_path = out_dir / f"v{version}_{name}.zip"

    if output_path.exists():
        output_path.unlink()

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
        zipf.write("build/merged-binary.bin", arcname="merged-binary.bin")
    print(f"zip bin to {output_path} done")

################################################################################
# board related functions — single-board project
################################################################################

_BOARDS_DIR = Path("main/boards")

# main/CMakeLists.txt 现在是单板硬编码:
#   target_compile_definitions(... BOARD_TYPE=\"zectrix-s3-epaper-4.2\" ...)
_CMAKE_BOARD_RE = re.compile(r'BOARD_TYPE=\\"([A-Za-z0-9._\-]+)\\"')


def _parse_board_type_from_cmake() -> Optional[str]:
    """Parse the hardcoded BOARD_TYPE from main/CMakeLists.txt."""
    cmake_file = Path("main/CMakeLists.txt")
    if not cmake_file.exists():
        return None
    m = _CMAKE_BOARD_RE.search(cmake_file.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def _board_type_exists(board_type: str) -> bool:
    """Single-board repo: the type must match main/CMakeLists.txt exactly."""
    return board_type == _parse_board_type_from_cmake()


def _collect_variants(config_filename: str = "config.json") -> list[dict[str, str]]:
    """Collect board/variant information under main/boards.

    Boards without a config.json are synthesized as a single variant
    (name == board dir name) when they match the hardcoded BOARD_TYPE.
    """
    variants: list[dict[str, str]] = []
    hardcoded = _parse_board_type_from_cmake()
    for board_path in _BOARDS_DIR.iterdir():
        if not board_path.is_dir():
            continue
        if board_path.name == "common":
            continue
        cfg_path = board_path / config_filename
        if not cfg_path.exists():
            if hardcoded and board_path.name == hardcoded:
                variants.append({"board": board_path.name, "name": board_path.name})
            continue
        try:
            with cfg_path.open() as f:
                cfg = json.load(f)
            for build in cfg.get("builds", []):
                variants.append({"board": board_path.name, "name": build["name"]})
        except Exception as e:
            print(f"[ERROR] 解析 {cfg_path} 失败: {e}", file=sys.stderr)
    return variants


# Kconfig "select" entries are not automatically applied when we simply append
# sdkconfig lines from config.json, so add the required dependencies here to
# mimic menuconfig behaviour.
_AUTO_SELECT_RULES: dict[str, list[str]] = {
    "CONFIG_USE_ESP_BLUFI_WIFI_PROVISIONING": [
        "CONFIG_BT_ENABLED=y",
        "CONFIG_BT_BLUEDROID_ENABLED=y",
        "CONFIG_BT_BLE_42_FEATURES_SUPPORTED=y",
        "CONFIG_BT_BLE_50_FEATURES_SUPPORTED=n",
        "CONFIG_BT_BLE_BLUFI_ENABLE=y",
        "CONFIG_MBEDTLS_DHM_C=y",
    ],
}


def _apply_auto_selects(sdkconfig_append: list[str]) -> list[str]:
    """Apply hardcoded auto-select rules to sdkconfig_append."""
    items: list[str] = []
    existing_keys: set[str] = set()

    def _append_if_missing(entry: str) -> None:
        key = entry.split("=", 1)[0]
        if key not in existing_keys:
            items.append(entry)
            existing_keys.add(key)

    # Preserve original order while tracking keys
    for entry in sdkconfig_append:
        _append_if_missing(entry)

    # Apply auto-select rules
    for key, deps in _AUTO_SELECT_RULES.items():
        for entry in sdkconfig_append:
            name, _, value = entry.partition("=")
            if name == key and value.lower().startswith("y"):
                for dep in deps:
                    _append_if_missing(dep)
                break

    return items


_APPEND_MARKER = "# Append by release.py"


def _append_sdkconfig(entries: list[str]) -> None:
    """Append entries to sdkconfig — idempotent.

    先移除上一次追加的整块再写新块(按键去重),反复运行不会堆积重复行;
    块外的 sdkconfig(用户手改/defaults 生成的)保持原样,追加块靠
    "后写覆盖" 语义生效。
    """
    path = Path("sdkconfig")
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if _APPEND_MARKER in existing:
        existing = existing.split(_APPEND_MARKER, 1)[0].rstrip("\n")

    dedup: dict[str, str] = {}
    for entry in entries:
        dedup[entry.split("=", 1)[0]] = entry

    block = _APPEND_MARKER + "\n" + "\n".join(dedup.values()) + "\n"
    text = (existing + "\n\n" + block) if existing.strip() else block
    path.write_text(text, encoding="utf-8")

################################################################################
# Compile implementation
################################################################################

def release(board_type: str, config_filename: str = "config.json", *,
            filter_name: Optional[str] = None, target: Optional[str] = None,
            ota_url: Optional[str] = None, wifi_ssid: Optional[str] = None,
            wifi_password: Optional[str] = None) -> None:
    """Compile and package the given board_type (single-board project).

    config.json is optional: when absent the board is built as one variant
    named after the board itself with the given target (default esp32s3).
    """
    cfg_path = _BOARDS_DIR / board_type / config_filename
    if cfg_path.exists():
        with cfg_path.open() as f:
            cfg = json.load(f)
        target = target or cfg["target"]
        builds = cfg.get("builds", [])
    else:
        print(f"[INFO] {cfg_path} 不存在,按单板默认变体构建 (name={board_type})")
        target = target or "esp32s3"
        builds = [{"name": board_type, "sdkconfig_append": []}]
    _validate("target", target, _SAFE_TARGET)

    project_version = get_project_version()
    print(f"Project Version: {project_version} ({cfg_path})")

    if filter_name:
        builds = [b for b in builds if b["name"] == filter_name]
        if not builds:
            print(f"[ERROR] 未在 {board_type} 中找到变体 {filter_name}", file=sys.stderr)
            sys.exit(1)

    for build in builds:
        name = build["name"]
        if not name.startswith(board_type):
            raise ValueError(f"build.name {name} 必须以 {board_type} 开头")
        _validate("build name", name, _SAFE_NAME)

        output_path = Path("releases") / f"v{project_version}_{name}.zip"
        if output_path.exists():
            print(f"跳过 {name} 因为 {output_path} 已存在")
            continue

        # 单板项目不再有 CONFIG_BOARD_TYPE_x 开关,直接用 CLI 注入项
        sdkconfig_append = list(build.get("sdkconfig_append", []))
        if ota_url:
            _validate("ota-url", ota_url, _SAFE_URL)
            sdkconfig_append.append(f'CONFIG_OTA_URL="{ota_url}"')
        if wifi_ssid:
            sdkconfig_append.append(f'CONFIG_DEFAULT_WIFI_SSID="{wifi_ssid}"')
            sdkconfig_append.append(f'CONFIG_DEFAULT_WIFI_PASSWORD="{wifi_password or ""}"')
        sdkconfig_append = _apply_auto_selects(sdkconfig_append)

        print("-" * 80)
        print(f"name: {name}")
        print(f"target: {target}")
        for item in sdkconfig_append:
            print(f"sdkconfig_append: {item}")

        os.environ.pop("IDF_TARGET", None)

        # Call set-target
        if os.system(f"idf.py set-target {shlex.quote(target)}") != 0:
            print("set-target failed", file=sys.stderr)
            sys.exit(1)

        # Append sdkconfig (先去重再写,幂等)
        _append_sdkconfig(sdkconfig_append)

        # Build with macro BOARD_NAME defined to name
        cmd = (f"idf.py -DBOARD_NAME={shlex.quote(name)} "
               f"-DBOARD_TYPE={shlex.quote(board_type)} build")
        if os.system(cmd) != 0:
            print("build failed")
            sys.exit(1)

        # merge-bin
        merge_bin()

        # Zip
        zip_bin(name, project_version)

################################################################################
# CLI entry
################################################################################

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("board", nargs="?", default=None,
                        help="板子类型(须与 main/CMakeLists.txt 的 BOARD_TYPE 一致)或 all")
    parser.add_argument("-c", "--config", default="config.json",
                        help="指定 config 文件名，默认 config.json(单板项目可无)")
    parser.add_argument("--list-boards", action="store_true", help="列出所有支持的 board 及变体列表")
    parser.add_argument("--json", action="store_true", help="配合 --list-boards，JSON 格式输出")
    parser.add_argument("--name", help="指定变体名称，仅编译匹配的变体")
    parser.add_argument("--target", default="esp32s3", help="芯片目标(config.json 缺省时使用,默认 esp32s3)")
    parser.add_argument("--ota-url", default=None, help="注入 CONFIG_OTA_URL")
    parser.add_argument("--wifi-ssid", default=None, help="注入 CONFIG_DEFAULT_WIFI_SSID")
    parser.add_argument("--wifi-password", default=None, help="注入 CONFIG_DEFAULT_WIFI_PASSWORD")

    args = parser.parse_args()

    # List mode
    if args.list_boards:
        variants = _collect_variants(config_filename=args.config)
        if args.json:
            print(json.dumps(variants))
        else:
            for v in variants:
                print(f"{v['board']}: {v['name']}")
        sys.exit(0)

    # Current directory firmware packaging mode
    if args.board is None:
        merge_bin()
        curr_board_type = get_board_type_from_compile_commands()
        if curr_board_type is None:
            print("未能从 compile_commands.json 解析 board_type", file=sys.stderr)
            sys.exit(1)
        project_ver = get_project_version()
        zip_bin(curr_board_type, project_ver)
        sys.exit(0)

    # Compile mode
    board_type_input: str = args.board
    name_filter: str | None = args.name

    if board_type_input == "all":
        # 单板仓库:all 等价于 CMakeLists 里那个板
        parsed = _parse_board_type_from_cmake()
        if not parsed:
            print("[ERROR] 未能从 main/CMakeLists.txt 解析 BOARD_TYPE", file=sys.stderr)
            sys.exit(1)
        board_type_input = parsed

    # Check board_type in CMakeLists (target_compile_definitions 硬编码单板)
    if not _board_type_exists(board_type_input):
        print(f"[ERROR] main/CMakeLists.txt 中未找到 board_type {board_type_input}",
              file=sys.stderr)
        sys.exit(1)

    release(board_type_input, config_filename=args.config,
            filter_name=name_filter, target=args.target,
            ota_url=args.ota_url, wifi_ssid=args.wifi_ssid,
            wifi_password=args.wifi_password)
