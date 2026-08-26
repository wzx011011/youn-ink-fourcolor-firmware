# 墨水屏看板/图片转换服务(纯 PIL 渲染,无浏览器依赖)
# 基础镜像:python 3.12
FROM python:3.12

ENV PYTHONUNBUFFERED=1
# volume 挂载的代码不生成 .pyc,避免改代码后加载旧缓存
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Layer 1: 中文字体(几乎不变)—— Noto CJK 供 PIL 画中文
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
        && rm -rf /var/lib/apt/lists/*

# Layer 2: Python 依赖(仅改动 requirements.txt 时重装)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# Layer 3: 应用代码(volume 挂载会覆盖本层,列在这里保证裸镜像可用)
COPY app.py .
COPY board/ board/
COPY templates/ templates/

# 数据卷:转换历史 / 调度配置持久化
RUN mkdir -p /data/uploads
VOLUME /data

EXPOSE 8096

# 1 worker:PIL 渲染线程安全且轻量,足够;180s 兜底超时
CMD ["gunicorn", "--bind", "0.0.0.0:8096", "--workers", "1", "--timeout", "180", "--access-logfile", "-", "app:app"]
