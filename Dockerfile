# ===========================
# 第一阶段：构建依赖
# ===========================
FROM python:3.10-slim-bookworm AS builder

WORKDIR /app

# 安装编译依赖（合并到一层以减小镜像体积）
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    build-essential \
    git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 复制并安装 Python 依赖
COPY requirements.txt .
RUN pip3 install --no-cache-dir --user -r requirements.txt

# ===========================
# 第二阶段：运行环境
# ===========================
FROM python:3.10-slim-bookworm

WORKDIR /app

# 只安装运行时必要的工具
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 从构建阶段复制已安装的 Python 包
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# 复制应用代码（使用 .dockerignore 优化）
COPY *.py ./
COPY config.py ./
COPY database/ ./database/
COPY plugins/ ./plugins/
COPY web/ ./web/

# 验证关键文件（可选，调试用）
RUN echo "=== 验证文件结构 ===" && \
    ls -la /app/ && \
    ls -la /app/web/ && \
    test -f /app/web/static/index.html && echo "✅ index.html 存在" || echo "❌ index.html 缺失" && \
    test -f /app/main.py && echo "✅ main.py 存在" || echo "❌ main.py 缺失"

# 暴露端口
EXPOSE 8585

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8585/health || exit 1

# 使用非 root 用户运行（安全性提升）
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app
USER appuser

# 启动应用
CMD ["python3", "main.py"]
