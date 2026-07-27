# 墨水屏图片转换服务 + 看板渲染(Playwright 截图)
# 基础镜像:python 3.12(非 slim,装 Chromium/字体依赖更顺)
FROM python:3.12

# 国内镜像加速 Playwright Chromium 二进制下载
ENV PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# ============================================================
# 分层缓存策略(从最少变动 → 最常变动)
# 这样改 app.py/模板时,前面 4 层全部命中缓存,只重跑最后的 COPY
# 只有升级 Python 依赖或 Chromium 时才会触发前面的重装
# ============================================================

# Layer 1: 系统依赖 —— 几乎不变(换国内 apt 源加速,deb.debian.org 国内巨慢)
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null \
    || sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list 2>/dev/null \
    || true
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
        fonts-wqy-zenhei \
        fonts-noto-color-emoji \
        && rm -rf /var/lib/apt/lists/*

# Layer 2: Python 依赖 —— 只在改 requirements.txt 时变
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# Layer 3: Playwright Chromium —— 只在升级 Playwright 时变(最重的一层 ~1.3GB)
RUN playwright install --with-deps chromium

# Layer 4: 应用代码 —— 最常变动(改这里只重跑这层,秒级)
# 注意:先 COPY 不常变的,后 COPY 常变的
COPY app.py .
COPY board/ board/
COPY templates/ templates/

# 数据卷
RUN mkdir -p /data/uploads
VOLUME /data

EXPOSE 8096

# gunicorn:1 worker(Chromium 占内存大),180s 超时(截图可能慢)
CMD ["gunicorn", "--bind", "0.0.0.0:8096", "--workers", "1", "--timeout", "180", "--access-logfile", "-", "app:app"]
