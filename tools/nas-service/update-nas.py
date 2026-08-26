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
  python tools/nas-service/update-nas.py
"""

import paramiko
import os
import sys

NAS_HOST = "192.168.100.78"
NAS_USER = "wzx"
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
    "board/registry.py",
    "board/config_store.py",
    "board/scheduler.py",
    "board/pil_renderer.py",
    "templates/index.html",
    "templates/detail.html",
]


def main():
    print(f"=== 同步代码到 NAS ({NAS_HOST}) ===\n")

    # 1. SFTP 上传(Synology chroot:/docker = /volume1/docker)
    transport = paramiko.Transport((NAS_HOST, 22))
    transport.connect(username=NAS_USER, password=NAS_PASS)
    sftp = paramiko.SFTPClient.from_transport(transport)
    # SFTP 路径:去掉 /volume1 前缀
    sftp_base = NAS_DEPLOY_DIR.replace("/volume1", "")

    print("上传文件:")
    for rel in SYNC_FILES:
        local = os.path.join(LOCAL, rel.replace("/", os.sep))
        remote = f"{sftp_base}/{rel}"
        sftp.put(local, remote)
        print(f"  ✓ {rel}")
    sftp.close()
    transport.close()

    # 2. SSH: docker cp 文件进容器(容器只挂载了 data 卷,代码是 COPY 进镜像的)
    #    然后重启容器让新代码生效
    print("\n拷贝文件进容器 + 重启...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(NAS_HOST, port=22, username=NAS_USER, password=NAS_PASS, timeout=10)

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

    # 3. 验证
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
