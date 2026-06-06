# syntax=docker/dockerfile:1.7
# ============================================================
# Portable Hermes Agent — Mavis + 本地 LLM + 云端 LLM
# ============================================================
# 构建：docker buildx build --platform linux/amd64 \
#   --output type=docker,dest=image.tar -t portable-hermes:latest .
# 压缩：docker save portable-hermes:latest | gzip -9 > image.tar.gz

# ====================== 阶段 1：builder ======================
FROM python:3.11-slim AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc g++ gfortran git curl wget ca-certificates unzip \
        libffi-dev libssl-dev libbz2-dev libreadline-dev libsqlite3-dev \
        liblzma-dev zlib1g-dev \
        nodejs npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# 1) Python wheels 预编译（cache 友好）
COPY requirements.txt ./
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# 2) Node modules
COPY package.json package-lock.json* ./
RUN npm ci --omit=dev 2>/dev/null || npm install --omit=dev

# 3) Mavis 本体（如果有本地源码）
COPY mavis/ ./mavis/
COPY hermes/ ./hermes/

# 4) 下载 llama.cpp 静态二进制（OpenAI-compat server）
ARG LLAMA_CPP_VERSION=b5170
RUN mkdir -p /opt/llama-bin && cd /opt/llama-bin && \
    curl -fsSL "https://github.com/ggerganov/llama.cpp/releases/download/${LLAMA_CPP_VERSION}/llama-${LLAMA_CPP_VERSION}-bin-linux-x86_64.zip" \
        -o llama.zip && \
    unzip llama.zip && \
    rm llama.zip && \
    chmod +x llama-${LLAMA_CPP_VERSION}/bin/* && \
    ls -la llama-${LLAMA_CPP_VERSION}/bin/


# ====================== 阶段 2：runtime ======================
FROM python:3.11-slim AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NODE_ENV=production \
    HERMES_HOME=/data \
    HERMES_CONFIG=/data/config/hermes.yaml \
    # === LLM 路由默认值 ===
    HERMES_LLM_MODE=hybrid \
    HERMES_NETWORK=auto \
    HERMES_LOCAL_LLM_URL=http://127.0.0.1:8080 \
    HERMES_LOCAL_LLM_PORT=8080

# 系统运行时依赖（无编译器）
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 libstdc++6 libgcc-s1 \
        libffi8 libssl3 libsqlite3-0 libcurl4 \
        nodejs npm tini gosu ca-certificates curl wget \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -s /bin/bash -u 1000 hermes \
    && mkdir -p /data /opt/mavis /opt/llama /opt/hermes \
    && chown -R hermes:hermes /data /opt/mavis /opt/llama /opt/hermes

# 1) Python 依赖（从 builder 拷 wheels，--no-index 完全离线）
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/*.whl \
    && rm -rf /wheels ~/.cache/pip

# 2) Node modules
COPY --from=builder /build/node_modules /opt/hermes/node_modules
COPY --from=builder /build/package.json /opt/hermes/

# 3) Mavis 本体（如有本地源码）
COPY --from=builder /build/mavis /opt/mavis
COPY --from=builder /build/hermes /opt/hermes/hermes
RUN if [ -f /opt/mavis/pyproject.toml ]; then \
        pip install --no-cache-dir -e /opt/mavis; \
    fi && \
    if [ -f /opt/hermes/hermes/pyproject.toml ]; then \
        pip install --no-cache-dir -e /opt/hermes/hermes; \
    fi

# 4) llama.cpp 二进制（OpenAI-compat server）
ARG LLAMA_CPP_VERSION=b5170
COPY --from=builder /opt/llama-bin /opt/llama
RUN ln -sf /opt/llama/llama-${LLAMA_CPP_VERSION}/bin/llama-server /usr/local/bin/llama-server && \
    ln -sf /opt/llama/llama-${LLAMA_CPP_VERSION}/bin/llama-cli /usr/local/bin/llama-cli && \
    ln -sf /opt/llama/llama-${LLAMA_CPP_VERSION}/bin/llama-embedding /usr/local/bin/llama-embedding && \
    mkdir -p /opt/llama/logs && \
    chown -R hermes:hermes /opt/llama

# 5) 入口脚本 + 路由器
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY docker/llama-router.sh /usr/local/bin/llama-router.sh
COPY docker/llama-supervisor.sh /usr/local/bin/llama-supervisor.sh
RUN chmod +x /usr/local/bin/*.sh

# 6) 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:7860/healthz || exit 1

# 7) 默认端口暴露
EXPOSE 7860 8080

WORKDIR /data
USER hermes

# tini 是 PID 1，正确转发信号、回收僵尸进程
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["serve", "--host", "0.0.0.0", "--port", "7860"]
