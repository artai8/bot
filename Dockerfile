# ===========================
# 阶段 1: 构建依赖
# ===========================
FROM python:3.10.20-slim-bookworm AS builder

WORKDIR /build

# 只装编译必需的包（build-essential 已含 gcc/g++，无需重复）
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# 用 --prefix 安装到独立目录并清理不需要的缓存、测试和字节码。
RUN pip install --no-cache-dir \
    --prefix=/install \
    --disable-pip-version-check \
    --no-warn-script-location \
    -r requirements.txt

RUN find /install -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null; \
    find /install -type d -name "tests"       -exec rm -rf {} + 2>/dev/null; \
    find /install -type d -name "test"        -exec rm -rf {} + 2>/dev/null; \
    find /install -type f -name "*.pyc"  -delete 2>/dev/null; \
    find /install -type f -name "*.pyo"  -delete 2>/dev/null; \
    true

# ===========================
# 阶段 2: 最小运行环境
# ===========================
FROM python:3.10.20-slim-bookworm

WORKDIR /app

# 安装运行时依赖并创建非 root 用户（合并为单层）。
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* && \
    useradd -m -u 1000 -s /bin/bash appuser && \
    chown -R appuser:appuser /app

# 将 Python 依赖包合并到系统路径（解决 root/.local 权限问题）
COPY --from=builder /install /usr/local

# 低内存 & 低 CPU 优化环境变量。注释放在 ENV 指令外，避免续行解析歧义。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MALLOC_ARENA_MAX=2 \
    MALLOC_MMAP_THRESHOLD_=65536 \
    MALLOC_TRIM_THRESHOLD_=131072

# 复制应用代码（按修改频率从低到高排序，最大化缓存命中）
COPY --chown=appuser:appuser config.py ./
COPY --chown=appuser:appuser database/ ./database/
COPY --chown=appuser:appuser plugins/ ./plugins/
COPY --chown=appuser:appuser web/ ./web/
COPY --chown=appuser:appuser scripts/ ./scripts/
COPY --chown=appuser:appuser *.py ./

USER appuser

EXPOSE 7860

CMD ["python3", "main.py"]
