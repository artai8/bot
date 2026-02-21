# ===========================
# 阶段 1: 构建依赖
# ===========================
FROM python:3.10-slim-bookworm AS builder

WORKDIR /app

# 只安装必要的编译工具（减少层大小）
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        build-essential \
        git && \
    rm -rf /var/lib/apt/lists/*

# 先复制 requirements.txt（利用缓存）
COPY requirements.txt .

# 优化 pip 安装：减少内存占用
RUN pip3 install --no-cache-dir --user \
    --disable-pip-version-check \
    --no-warn-script-location \
    --compile \
    -r requirements.txt && \
    # 立即清理 pip 缓存
    rm -rf /root/.cache/pip /tmp/*

# ===========================
# 阶段 2: 运行环境
# ===========================
FROM python:3.10-slim-bookworm

WORKDIR /app

# 只安装运行时必要的工具
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# 从构建阶段复制 Python 包
COPY --from=builder /root/.local /root/.local

# 设置环境变量
ENV PATH=/root/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 创建非 root 用户（提前创建，避免后续权限问题）
RUN useradd -m -u 1000 -s /bin/bash appuser && \
    chown -R appuser:appuser /app

# 复制应用代码（按修改频率从低到高排序）
COPY --chown=appuser:appuser config.py ./
COPY --chown=appuser:appuser database/ ./database/
COPY --chown=appuser:appuser plugins/ ./plugins/
COPY --chown=appuser:appuser web/ ./web/
COPY --chown=appuser:appuser *.py ./

# 切换到非 root 用户
USER appuser

# 暴露端口
EXPOSE 8585

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=40s \
    CMD curl -f http://localhost:8585/health || exit 1

# 启动应用
CMD ["python3", "main.py"]
