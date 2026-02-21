FROM python:3.10-slim-bookworm AS builder
WORKDIR /app

# 安装 gcc 编译器 + git（TgCrypto 编译需要，git 用于从 GitHub 安装包）
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc build-essential git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip3 install --no-cache-dir --user -r requirements.txt

FROM python:3.10-slim-bookworm
WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# 分步复制，确保目录结构正确
COPY *.py ./
COPY config.py ./
COPY database/ ./database/
COPY plugins/ ./plugins/
COPY web/ ./web/

# 验证关键文件存在
RUN echo "=== Verifying file structure ===" && \
    ls -la /app/ && \
    ls -la /app/web/ && \
    ls -la /app/web/static/ && \
    ls -la /app/web/static/css/ && \
    ls -la /app/web/static/js/ && \
    test -f /app/web/static/index.html && echo "✅ index.html found" || echo "❌ index.html MISSING"

EXPOSE 8585

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8585/health || exit 1

CMD ["python3", "main.py"]
