# ===========================
# 阶段 1: 构建依赖
# ===========================
FROM python:3.10-slim-bookworm AS builder

WORKDIR /build

# 只装编译必需的包（build-essential 已含 gcc/g++，无需重复）
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# 用 --prefix 安装到独立目录，后面直接合并到 /usr/local
RUN pip install --no-cache-dir \
    --prefix=/install \
    --disable-pip-version-check \
    --no-warn-script-location \
    -r requirements.txt && \
    # 清理安装包中不必要的文件，减小镜像体积
    find /install -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null; \
    find /install -type d -name "tests"       -exec rm -rf {} + 2>/dev/null; \
    find /install -type d -name "test"        -exec rm -rf {} + 2>/dev/null; \
    find /install -type f -name "*.pyc"  -delete 2>/dev/null; \
    find /install -type f -name "*.pyo"  -delete 2>/dev/null; \
    find /install -type f -name "*.dist-info/RECORD" -delete 2>/dev/null; \
    true

# ===========================
# 阶段 2: 最小运行环境
# ===========================
FROM python:3.10-slim-bookworm

WORKDIR /app

# 安装运行时依赖 + 创建用户（合并为单层）
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* && \
    # 创建非 root 用户
    useradd -m -u 1000 -s /bin/bash appuser && \
    chown -R appuser:appuser /app

# 将 Python 依赖包合并到系统路径（解决 root/.local 权限问题）
COPY --from=builder /install /usr/local

# 低内存 & 低 CPU 优化环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # 限制 glibc 内存池数量，大幅减少内存碎片（默认=CPU核心数*8）
    MALLOC_ARENA_MAX=2 \
    # 减少 malloc 的 mmap 阈值，让小块内存更快归还系统
    MALLOC_MMAP_THRESHOLD_=65536 \
    # 更积极地释放内存回操作系统
    MALLOC_TRIM_THRESHOLD_=131072

# 复制应用代码（按修改频率从低到高排序，最大化缓存命中）
COPY --chown=appuser:appuser config.py ./
COPY --chown=appuser:appuser database/ ./database/
COPY --chown=appuser:appuser plugins/ ./plugins/
COPY --chown=appuser:appuser web/ ./web/
COPY --chown=appuser:appuser *.py ./

USER appuser

EXPOSE 8585

# 健康检查（拉长间隔减少 CPU 开销，-sf 静默 + 失败返回错误码）
HEALTHCHECK --interval=60s --timeout=5s --retries=3 --start-period=45s \
    CMD curl -sf http://localhost:8585/health || exit 1

CMD ["python3", "main.py"]
