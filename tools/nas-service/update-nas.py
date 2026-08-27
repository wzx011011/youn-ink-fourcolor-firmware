#!/usr/bin/env python3
"""
日常更新脚本:把改动的代码同步到 NAS 并重启容器(秒级,不重建镜像)。

适用场景:
  - 改了 board/templates/*.html(调样式)
  - 改了 board/*.py(数据源)
  - 改了 app.py(路由逻辑)
  - 加了新看板

不适用(需要重建镜像,用 deploy.sh):
  - 改了 requirements.txt(加新 Python 库)
  - 改了 Dockerfile
  - 重建镜像(如 Dockerfile/依赖变更)

用法(在开发机):
  export NAS_PASS='...'
  python tools/nas-service/update-nas.py [--trust-host]

首次连接若 known_hosts 里没有这台 NAS,SSH 会被拒绝(防中间人):
  - 推荐:ssh-keyscan -H <NAS_IP> >> ~/.ssh/known_hosts 后重跑
  - 或加 --trust-host 显式信任并自动记录本次 host key
"""

import argparse
import os
import sys

import paramiko

NAS_HOST = os.environ.get("NAS_HOST", "192.168.100.78")
NAS_USER = os.environ.get("NAS_USER", "wzx")
NAS_PASS = os.environ.get("NAS_PASS", "")  # 设置环境变量 NAS_PASS,不要硬编码
NAS_DEPLOY_DIR = "/volume1/docker/eink-photo"
DOCKER = "/var/packages/ContainerManager/target/usr/bin/docker"

LOCAL = os.path.dirname(os.path.abspath(__file__))

# 要同步的文件(相对路径)
SYNC_FILES = [
    "app.py",
    "board/__init__.py",
    "board/almanac.py",
    "board/news.py",
    "board/weather.py",
    "board/stock.py",
    "board/chat.py",
    "board/registry.py",
    "board/config_store.py",
    "board/scheduler.py",
    "board/pil_renderer.py",
    "templates/index.html",
    "templates/detail.html",
]


def main():
    ap = argparse.ArgumentParser(description="同步 nas-service 代码到 NAS 并重启容器")
    ap.add_argument("--trust-host", action="store_true",
                    help="显式信任当前 NAS 的 host key 并自动加入 known_hosts"
                         "(仅首次部署或 NAS 重装系统后使用)")
    args = ap.parse_args()

    if not NAS_PASS:
        sys.exit("[ERROR] 未设置 NAS_PASS 环境变量。请先执行:\n"
                 "  export NAS_PASS='你的NAS密码'\n"
                 "再运行本脚本(不要把密码写进代码)。")

    print(f"=== 同步代码到 NAS ({NAS_HOST}) ===\n")

    # 1. SSH 连接(统一走 SSHClient,host key 校验对 SFTP/exec 都生效)
    client = paramiko.SSHClient()
    client.load_system_host_keys()  # 已知主机照常信任
    if args.trust_host:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    else:
        # 默认拒绝未知 host key,防止中间人;首次连接见文件头说明
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(NAS_HOST, port=22, username=NAS_USER, password=NAS_PASS,
                       timeout=10, allow_agent=False, look_for_keys=False)
    except paramiko.SSHException as e:
        sys.exit(f"[ERROR] SSH 连接失败: {e}\n"
                 f"  若是首次连接(known_hosts 无 {NAS_HOST}),两种处理:\n"
                 f"    a) ssh-keyscan -H {NAS_HOST} >> ~/.ssh/known_hosts 后重跑\n"
                 f"    b) 加 --trust-host 显式信任并自动记录本次 host key")

    # 2. SFTP 上传(Synology chroot:/docker = /volume1/docker)
    sftp = client.open_sftp()
    # SFTP 路径:去掉 /volume1 前缀
    sftp_base = NAS_DEPLOY_DIR.replace("/volume1", "")

    print("上传文件:")
    for rel in SYNC_FILES:
        local = os.path.join(LOCAL, rel.replace("/", os.sep))
        remote = f"{sftp_base}/{rel}"
        sftp.put(local, remote)
        print(f"  ✓ {rel}")
    sftp.close()

    # 3. docker cp 文件进容器(容器只挂载了 data 卷,代码是 COPY 进镜像的)
    #    然后重启容器让新代码生效
    print("\n拷贝文件进容器 + 重启...")
    for rel in SYNC_FILES:
        # 宿主路径 -> 容器内路径
        host_path = f"{NAS_DEPLOY_DIR}/{rel}"
        container_path = f"/app/{rel}"
        stdin, stdout, stderr = client.exec_command(
            f"{DOCKER} cp {host_path} eink-photo:{container_path} 2>&1", timeout=15
        )
        err = stderr.read().decode().strip()
        if err:
            print(f"  ⚠️  {rel}: {err}")

    # 重启容器让新代码生效
    stdin, stdout, stderr = client.exec_command(
        f"cd {NAS_DEPLOY_DIR} && {DOCKER} compose restart 2>&1", timeout=60
    )
    print(stdout.read().decode())

    # 4. 验证
    import time; time.sleep(5)
    stdin, stdout, stderr = client.exec_command(
        f"{DOCKER} ps --format '{{{{.Names}}}} {{{{.Status}}}}' | grep eink", timeout=15
    )
    status = stdout.read().decode().strip()
    print(f"容器状态: {status}")

    client.close()
    print(f"\n✅ 完成。访问 http://{NAS_HOST}:8848/ 验证效果。")


if __name__ == "__main__":
    main()
