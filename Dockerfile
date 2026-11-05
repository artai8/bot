# ==================================
# 阶段 1: 构建依赖
# ==================================
FROM python:3.10.20-slim-bookworm AS builder

WORKDIR /build

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

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

# ==================================
# 阶段 2: 最小运行环境
# ==================================
FROM python:3.10.20-slim-bookworm

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* && \
    useradd -m -u 1000 -s /bin/bash appuser && \
    chown -R appuser:appuser /app

COPY --from=builder /install /usr/local

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MALLOC_ARENA_MAX=2 \
    MALLOC_MMAP_THRESHOLD_=65536 \
    MALLOC_TRIM_THRESHOLD_=131072

COPY --chown=appuser:appuser database/ ./database/
COPY --chown=appuser:appuser plugins/ ./plugins/
COPY --chown=appuser:appuser web/ ./web/
COPY --chown=appuser:appuser scripts/ ./scripts/
COPY --chown=appuser:appuser *.py ./

USER appuser

EXPOSE 7860

CMD ["python3", "main.py"]
